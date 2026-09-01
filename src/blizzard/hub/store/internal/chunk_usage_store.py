"""SQLAlchemy adapter for the chunk usage seam (package-private, blizzard#411 Phase 3).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.usage import IWriteChunkUsageRepository
from blizzard.hub.domain.work import UsageFact
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections


class ChunkUsageStore:
    """The chunk's token/cost usage facts."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def usage_since(self, since: datetime, *, until: datetime | None = None) -> list[UsageFact]:
        with self._store.read("usage_since") as conn:
            query = select(s.usage_facts).where(s.usage_facts.c.recorded_at >= since)
            if until is not None:
                query = query.where(s.usage_facts.c.recorded_at < until)
            return [
                UsageFact(
                    node_id=u.node_id,
                    epoch=u.epoch,
                    kind=u.kind,
                    model=u.model,
                    input_tokens=u.input_tokens,
                    output_tokens=u.output_tokens,
                    cache_read_tokens=u.cache_read_tokens,
                    cache_create_tokens=u.cache_create_tokens,
                    cost_usd=u.cost_usd,
                    recorded_at=u.recorded_at,
                )
                for u in conn.execute(query).all()
            ]

    def record_usage(
        self,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        runner_id: str,
        kind: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_create_tokens: int,
        cost_usd: float | None,
        at: datetime,
    ) -> None:
        # Append-only, no epoch fence, no second dedup key — the caller's per-runner seq
        # high-water mark already guarantees at most one call per landed fact.
        with self._store.write("record_usage") as conn:
            conn.execute(
                s.usage_facts.insert().values(
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    runner_id=runner_id,
                    kind=kind,
                    model=model,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_create_tokens=cache_create_tokens,
                    cost_usd=cost_usd,
                    recorded_at=at,
                )
            )


def _conforms_usage(x: ChunkUsageStore) -> IWriteChunkUsageRepository:
    return x
