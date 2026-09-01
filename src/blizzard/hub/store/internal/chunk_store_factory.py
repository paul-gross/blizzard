"""Builds the 15 chunk-seam adapters as one :class:`ChunkStores` bundle (package-private)
— the one place their construction order is expressed, so ``hub/composition.py`` and a
component test's own store-level fixture wire the identical shape rather than each
re-deriving it."""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.stores import ChunkStores
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_artifacts_store import ChunkArtifactsStore
from blizzard.hub.store.internal.chunk_decisions_store import ChunkDecisionsStore
from blizzard.hub.store.internal.chunk_delivery_store import ChunkDeliveryStore
from blizzard.hub.store.internal.chunk_escalations_store import ChunkEscalationsStore
from blizzard.hub.store.internal.chunk_events_store import ChunkEventsStore
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_hub_exec_store import ChunkHubExecStore
from blizzard.hub.store.internal.chunk_lifecycle_store import ChunkLifecycleStore
from blizzard.hub.store.internal.chunk_movement_store import ChunkMovementStore
from blizzard.hub.store.internal.chunk_questions_store import ChunkQuestionsStore
from blizzard.hub.store.internal.chunk_queue_store import ChunkQueueStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.internal.chunk_usage_store import ChunkUsageStore
from blizzard.hub.store.internal.chunk_work_refs_store import ChunkWorkRefsStore


def build_chunk_stores(store: HubStoreConnections, clock: IClock) -> ChunkStores:
    """All 15 chunk-seam adapters over one connection seam and clock. ``facts`` is built
    first since ``record``/``work_refs``/``escalations`` each hold it as their own read
    collaborator."""
    facts = ChunkFactsStore(store, clock)
    return ChunkStores(
        facts=facts,
        record=ChunkRecordStore(store, clock, facts=facts),
        lifecycle=ChunkLifecycleStore(store, clock),
        work_refs=ChunkWorkRefsStore(store, clock, facts=facts),
        queue=ChunkQueueStore(store, clock),
        route=ChunkRouteStore(store, clock),
        movement=ChunkMovementStore(store, clock),
        artifacts=ChunkArtifactsStore(store, clock),
        questions=ChunkQuestionsStore(store, clock),
        decisions=ChunkDecisionsStore(store, clock),
        escalations=ChunkEscalationsStore(store, clock, facts=facts),
        events=ChunkEventsStore(store, clock),
        usage=ChunkUsageStore(store, clock),
        delivery=ChunkDeliveryStore(store, clock),
        hub_exec=ChunkHubExecStore(store, clock),
    )
