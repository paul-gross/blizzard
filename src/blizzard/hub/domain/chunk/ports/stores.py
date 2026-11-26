"""The chunk-store bundles. :class:`ChunkStores` is the tenant's one set of chunk-seam adapters, which each chunk
port resolves a field of. No ``src/`` domain collaborator takes the bundle: every service names its seams explicitly,
so no chunk service can hold write access to a concept it never calls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from blizzard.hub.domain.chunk.ports.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunk.ports.dependencies import (
    IWriteChunkDependenciesRepository,
)
from blizzard.hub.domain.chunk.ports.escalations import (
    IWriteChunkEscalationsRepository,
)
from blizzard.hub.domain.chunk.ports.events import IWriteChunkEventsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.domain.chunk.ports.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.questions import IWriteChunkQuestionsRepository
from blizzard.hub.domain.chunk.ports.queue import IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IWriteChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.chunk.ports.usage import IWriteChunkUsageRepository
from blizzard.hub.domain.chunk.ports.work_refs import IWriteChunkWorkRefsRepository

__all__ = ["ChunkStores", "IChunkStoreBundle"]


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
