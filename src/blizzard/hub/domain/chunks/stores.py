"""The chunk-store bundles (D1) — composition-and-test handles for a
collaborator spanning several chunk seams. Unlike :class:`~blizzard.runner.stores.RunnerStores`,
no ``src/`` domain collaborator takes either bundle: every service names its seams explicitly,
so no chunk service can hold write access to a concept it never calls. Declares no umbrella
Protocol either — unlike the runner's, nothing would hold one."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.domain.chunks.artifacts import IReadChunkArtifactsRepository, IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunks.decisions import IReadChunkDecisionsRepository, IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunks.delivery import IReadChunkDeliveryRepository, IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunks.escalations import IReadChunkEscalationsRepository, IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunks.events import IReadChunkEventsRepository, IWriteChunkEventsRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.hub_exec import IReadChunkHubExecRepository, IWriteChunkHubExecRepository
from blizzard.hub.domain.chunks.lifecycle import IReadChunkLifecycleRepository, IWriteChunkLifecycleRepository
from blizzard.hub.domain.chunks.movement import IReadChunkMovementRepository, IWriteChunkMovementRepository
from blizzard.hub.domain.chunks.questions import IReadChunkQuestionsRepository, IWriteChunkQuestionsRepository
from blizzard.hub.domain.chunks.queue import IReadChunkQueueRepository, IWriteChunkQueueRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository, IWriteChunkRecordRepository
from blizzard.hub.domain.chunks.route import IReadChunkRouteRepository, IWriteChunkRouteRepository
from blizzard.hub.domain.chunks.usage import IReadChunkUsageRepository, IWriteChunkUsageRepository
from blizzard.hub.domain.chunks.work_refs import IReadChunkWorkRefsRepository, IWriteChunkWorkRefsRepository

__all__ = ["ChunkReadStores", "ChunkStores"]


@dataclass(frozen=True)
class ChunkStores:
    """The wired chunk-seam collaborators, built by :func:`~blizzard.hub.composition.build_hub_services`."""

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


@dataclass(frozen=True)
class ChunkReadStores:
    """The controller-facing chunk-seam bundle — every field typed to its
    concept's read Protocol only, so ``bzh:controller-read-only`` is enforced at type-check
    time for the one collaborator every route handler reaches through. Built by
    :func:`~blizzard.hub.composition.build_hub_services` from the same per-seam adapter
    instances as :class:`ChunkStores`."""

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
