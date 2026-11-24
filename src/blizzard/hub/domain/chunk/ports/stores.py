"""The chunk-store bundles. :class:`ChunkStores` is the tenant's one set of chunk-seam adapters, which each chunk
port resolves a field of; :class:`ChunkReadStores` is its read-typed view. No ``src/`` domain collaborator takes either
bundle: every service names its seams explicitly, so no chunk service can hold write access to a concept it never
calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

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
from blizzard.hub.domain.chunk.ports.events import IReadChunkEventsRepository, IWriteChunkEventsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.hub_exec import IReadChunkHubExecRepository, IWriteChunkHubExecRepository
from blizzard.hub.domain.chunk.ports.lifecycle import IReadChunkLifecycleRepository, IWriteChunkLifecycleRepository
from blizzard.hub.domain.chunk.ports.movement import IReadChunkMovementRepository, IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.questions import IReadChunkQuestionsRepository, IWriteChunkQuestionsRepository
from blizzard.hub.domain.chunk.ports.queue import IReadChunkQueueRepository, IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository, IWriteChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository, IWriteChunkRouteRepository
from blizzard.hub.domain.chunk.ports.usage import IReadChunkUsageRepository, IWriteChunkUsageRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository, IWriteChunkWorkRefsRepository

__all__ = ["ChunkReadStores", "ChunkStores", "IChunkStoreBundle"]


class IChunkStoreBundle(Protocol):
    """The tenant's chunk-seam adapters as the chunk area registers its ports over them: each concept's write seam,
    read-only here so the root hands :class:`ChunkStores` in under this port."""

    @property
    def facts(self) -> IReadChunkFactsRepository: ...

    @property
    def record(self) -> IWriteChunkRecordRepository: ...

    @property
    def lifecycle(self) -> IWriteChunkLifecycleRepository: ...

    @property
    def work_refs(self) -> IWriteChunkWorkRefsRepository: ...

    @property
    def queue(self) -> IWriteChunkQueueRepository: ...

    @property
    def route(self) -> IWriteChunkRouteRepository: ...

    @property
    def movement(self) -> IWriteChunkMovementRepository: ...

    @property
    def artifacts(self) -> IWriteChunkArtifactsRepository: ...

    @property
    def questions(self) -> IWriteChunkQuestionsRepository: ...

    @property
    def decisions(self) -> IWriteChunkDecisionsRepository: ...

    @property
    def escalations(self) -> IWriteChunkEscalationsRepository: ...

    @property
    def events(self) -> IWriteChunkEventsRepository: ...

    @property
    def usage(self) -> IWriteChunkUsageRepository: ...

    @property
    def delivery(self) -> IWriteChunkDeliveryRepository: ...

    @property
    def hub_exec(self) -> IWriteChunkHubExecRepository: ...

    @property
    def dependencies(self) -> IWriteChunkDependenciesRepository: ...


@dataclass(frozen=True)
class ChunkStores:
    """The tenant's one set of chunk-seam adapters, built by the store wiring; the chunk area registers each field
    under its concept's ports, so a port and its field are one instance."""

    facts: IReadChunkFactsRepository
    record: IWriteChunkRecordRepository
    lifecycle: IWriteChunkLifecycleRepository
    work_refs: IWriteChunkWorkRefsRepository
    queue: IWriteChunkQueueRepository
    route: IWriteChunkRouteRepository
    movement: IWriteChunkMovementRepository
    artifacts: IWriteChunkArtifactsRepository
    questions: IWriteChunkQuestionsRepository
    decisions: IWriteChunkDecisionsRepository
    escalations: IWriteChunkEscalationsRepository
    events: IWriteChunkEventsRepository
    usage: IWriteChunkUsageRepository
    delivery: IWriteChunkDeliveryRepository
    hub_exec: IWriteChunkHubExecRepository
    dependencies: IWriteChunkDependenciesRepository


@dataclass(frozen=True)
class ChunkReadStores:
    """The read-typed view of the tenant's chunk stores that the legacy tenant services still carry for the readers
    not yet narrowed to the chunk read ports — every field typed to its concept's read Protocol only, so
    ``bzh:controller-read-only`` is enforced at type-check time, and each the instance its read port resolves to."""

    facts: IReadChunkFactsRepository
    record: IReadChunkRecordRepository
    lifecycle: IReadChunkLifecycleRepository
    work_refs: IReadChunkWorkRefsRepository
    queue: IReadChunkQueueRepository
    route: IReadChunkRouteRepository
    movement: IReadChunkMovementRepository
    artifacts: IReadChunkArtifactsRepository
    questions: IReadChunkQuestionsRepository
    decisions: IReadChunkDecisionsRepository
    escalations: IReadChunkEscalationsRepository
    events: IReadChunkEventsRepository
    usage: IReadChunkUsageRepository
    delivery: IReadChunkDeliveryRepository
    hub_exec: IReadChunkHubExecRepository
    dependencies: IReadChunkDependenciesRepository
