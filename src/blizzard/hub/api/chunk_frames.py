"""The reads a ``chunk-changed`` frame is enriched from, and the broker it is published on — one tenant's loader."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry
from blizzard.hub.events.broker import EventBroker


@dataclass(frozen=True)
class ChunkFrameLoader:
    """One tenant's chunk-frame reads — the chunk's facts, record and route, its graphs, and its runner's
    registered name — and the broker its frames and the queue's change are published on."""

    facts: IReadChunkFactsRepository
    record: IReadChunkRecordRepository
    route: IReadChunkRouteRepository
    graphs: IReadGraphRepository
    registry: IReadRunnerRegistry
    events: EventBroker
