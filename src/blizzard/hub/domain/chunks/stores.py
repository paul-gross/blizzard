"""The chunk-store bundle (blizzard#411, D1) — the composition-and-test handle for a
collaborator spanning several chunk seams. Unlike :class:`~blizzard.runner.stores.RunnerStores`,
no ``src/`` domain collaborator takes this bundle: every service names its seams explicitly, so
no chunk service can hold write access to a concept it never calls. Declares no umbrella
Protocol either — unlike the runner's, nothing would hold one."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.domain.chunks.artifacts import IWriteChunkArtifactsRepository
from blizzard.hub.domain.chunks.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunks.delivery import IWriteChunkDeliveryRepository
from blizzard.hub.domain.chunks.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunks.events import IWriteChunkEventsRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.hub_exec import IWriteChunkHubExecRepository
from blizzard.hub.domain.chunks.lifecycle import IWriteChunkLifecycleRepository
from blizzard.hub.domain.chunks.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunks.questions import IWriteChunkQuestionsRepository
from blizzard.hub.domain.chunks.queue import IWriteChunkQueueRepository
from blizzard.hub.domain.chunks.record import IWriteChunkRecordRepository
from blizzard.hub.domain.chunks.route import IWriteChunkRouteRepository
from blizzard.hub.domain.chunks.usage import IWriteChunkUsageRepository
from blizzard.hub.domain.chunks.work_refs import IWriteChunkWorkRefsRepository

__all__ = ["ChunkStores"]


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
