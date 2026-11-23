"""The chunk area's registrations: each chunk concept's port over the tenant's one chunk-store bundle, the flow
submissions and work-item reads ingest and the operations read, the event log and its live publisher, and the ingest
and dependency services; ingest's work-ref labels are the work-source area's.

Each concept has one owner — a factory over the bundle keyed by the concept's write port — and its read port is aliased
onto that owner, so a concept's read and write keys resolve to one instance. The ports another adapter answers to are
aliased onto the ``Ref`` the root hands in; the area names no adapter. The store-free graph declares each registration
absent, from the same roster."""

from __future__ import annotations

from typing import Any

from blizzard.foundation.registry.interface import Lifetime, Ref, Registry, port
from blizzard.hub.domain.chunk.dependencies import DependencyService
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.chunk.ingest import IngestService, IWorkItemProjectRead
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository, IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.decisions import IReadChunkDecisionsRepository, IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.delivery import IReadChunkDeliveryRepository, IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.dependencies import (
    IReadChunkDependenciesRepository,
    IWriteChunkDependenciesRepository,
)
from blizzard.hub.domain.chunk.ports.escalations import (
    IReadChunkEscalationsRepository,
    IWriteChunkEscalationsRepository,
)
from blizzard.hub.domain.chunk.ports.events import (
    IEventLogPublisher,
    IReadChunkEventsRepository,
    IWriteChunkEventsRepository,
)
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.flow_submissions import IReadChunkFlowSubmissions
from blizzard.hub.domain.chunk.ports.hub_exec import IReadChunkHubExecRepository, IWriteChunkHubExecRepository
from blizzard.hub.domain.chunk.ports.lifecycle import IReadChunkLifecycleRepository, IWriteChunkLifecycleRepository
from blizzard.hub.domain.chunk.ports.movement import IReadChunkMovementRepository, IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.questions import IReadChunkQuestionsRepository, IWriteChunkQuestionsRepository
from blizzard.hub.domain.chunk.ports.queue import IReadChunkQueueRepository, IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository, IWriteChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository, IWriteChunkRouteRepository
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from blizzard.hub.domain.chunk.ports.usage import IReadChunkUsageRepository, IWriteChunkUsageRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository, IWriteChunkWorkRefsRepository
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.tenancy import STORE_UNAVAILABLE


def chunk_facts(chunks: ChunkStores) -> IReadChunkFactsRepository:
    """The tenant's chunk-facts reads."""
    return chunks.facts


def chunk_record(chunks: ChunkStores) -> IWriteChunkRecordRepository:
    """The tenant's chunk records."""
    return chunks.record


def chunk_lifecycle(chunks: ChunkStores) -> IWriteChunkLifecycleRepository:
    """The tenant's chunk lifecycle."""
    return chunks.lifecycle


def chunk_work_refs(chunks: ChunkStores) -> IWriteChunkWorkRefsRepository:
    """The tenant's chunk work refs."""
    return chunks.work_refs


def chunk_queue(chunks: ChunkStores) -> IWriteChunkQueueRepository:
    """The tenant's chunk queue."""
    return chunks.queue


def chunk_route(chunks: ChunkStores) -> IWriteChunkRouteRepository:
    """The tenant's chunk routes."""
    return chunks.route


def chunk_movement(chunks: ChunkStores) -> IWriteChunkMovementRepository:
    """The tenant's chunk movement."""
    return chunks.movement


def chunk_artifacts(chunks: ChunkStores) -> IWriteChunkArtifactsRepository:
    """The tenant's chunk artifacts."""
    return chunks.artifacts


def chunk_questions(chunks: ChunkStores) -> IWriteChunkQuestionsRepository:
    """The tenant's chunk questions."""
    return chunks.questions


def chunk_decisions(chunks: ChunkStores) -> IWriteChunkDecisionsRepository:
    """The tenant's chunk decisions."""
    return chunks.decisions


def chunk_escalations(chunks: ChunkStores) -> IWriteChunkEscalationsRepository:
    """The tenant's chunk escalations."""
    return chunks.escalations


def chunk_events(chunks: ChunkStores) -> IWriteChunkEventsRepository:
    """The tenant's chunk event log records."""
    return chunks.events


def chunk_usage(chunks: ChunkStores) -> IWriteChunkUsageRepository:
    """The tenant's chunk usage."""
    return chunks.usage


def chunk_delivery(chunks: ChunkStores) -> IWriteChunkDeliveryRepository:
    """The tenant's chunk delivery."""
    return chunks.delivery


def chunk_hub_exec(chunks: ChunkStores) -> IWriteChunkHubExecRepository:
    """The tenant's chunk hub-executed node state."""
    return chunks.hub_exec


def chunk_dependencies(chunks: ChunkStores) -> IWriteChunkDependenciesRepository:
    """The tenant's chunk dependencies."""
    return chunks.dependencies


def event_log_publisher(events: EventBroker) -> IEventLogPublisher:
    """The live half of recording an event: the tenant's broker."""
    return events


#: Each chunk concept's owner over the bundle, and the read port aliased onto it — ``None`` where the owner's key is
#: the read port itself.
_CONCEPTS: tuple[tuple[Any, type[Any] | None], ...] = (
    (chunk_facts, None),
    (chunk_record, IReadChunkRecordRepository),
    (chunk_lifecycle, IReadChunkLifecycleRepository),
    (chunk_work_refs, IReadChunkWorkRefsRepository),
    (chunk_queue, IReadChunkQueueRepository),
    (chunk_route, IReadChunkRouteRepository),
    (chunk_movement, IReadChunkMovementRepository),
    (chunk_artifacts, IReadChunkArtifactsRepository),
    (chunk_questions, IReadChunkQuestionsRepository),
    (chunk_decisions, IReadChunkDecisionsRepository),
    (chunk_escalations, IReadChunkEscalationsRepository),
    (chunk_events, IReadChunkEventsRepository),
    (chunk_usage, IReadChunkUsageRepository),
    (chunk_delivery, IReadChunkDeliveryRepository),
    (chunk_hub_exec, IReadChunkHubExecRepository),
    (chunk_dependencies, IReadChunkDependenciesRepository),
)

#: The area's services, each registered by class.
_SERVICES: tuple[type[Any], ...] = (EventLogService, IngestService, DependencyService)


def register(
    registry: Registry,
    *,
    submissions: Ref[IReadChunkFlowSubmissions],
    work_items: Ref[IWorkItemProjectRead],
) -> None:
    """Declare each chunk concept's ports once per tenant, the flow submissions and work-item reads over the adapters
    handed in, the event log's publisher, and the area's services."""
    for owner, read in _CONCEPTS:
        target = registry.factory(owner, lifetime=Lifetime.TENANT)
        if read is not None:
            registry.alias(port(read), target)
    registry.alias(port(IReadChunkFlowSubmissions), submissions)
    registry.alias(port(IWorkItemProjectRead), work_items)
    registry.factory(event_log_publisher, lifetime=Lifetime.TENANT)
    for service in _SERVICES:
        registry.service(service, lifetime=Lifetime.TENANT)


#: Every tenant key :func:`register` declares, for the store-free graph to declare absent.
_TENANT_KEYS: tuple[type[Any], ...] = (
    IReadChunkFactsRepository,
    IWriteChunkRecordRepository,
    IWriteChunkLifecycleRepository,
    IWriteChunkWorkRefsRepository,
    IWriteChunkQueueRepository,
    IWriteChunkRouteRepository,
    IWriteChunkMovementRepository,
    IWriteChunkArtifactsRepository,
    IWriteChunkQuestionsRepository,
    IWriteChunkDecisionsRepository,
    IWriteChunkEscalationsRepository,
    IWriteChunkEventsRepository,
    IWriteChunkUsageRepository,
    IWriteChunkDeliveryRepository,
    IWriteChunkHubExecRepository,
    IWriteChunkDependenciesRepository,
    *(read for _, read in _CONCEPTS if read is not None),
    IReadChunkFlowSubmissions,
    IWorkItemProjectRead,
    IEventLogPublisher,
    *_SERVICES,
)


def register_absent(registry: Registry) -> None:
    """Declare the area's ports and services absent, for the store-free graph."""
    for missing in _TENANT_KEYS:
        registry.absent(port(missing), lifetime=Lifetime.TENANT, label=STORE_UNAVAILABLE)
