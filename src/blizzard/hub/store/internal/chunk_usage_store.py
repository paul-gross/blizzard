"""SQLAlchemy adapter for the chunk usage seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select, select

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.usage import IWriteChunkUsageRepository
from blizzard.hub.domain.work import UsageTotal
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.usage_aggregate import usage_aggregate_columns


def _usage_total_stmt(since: datetime, until: datetime | None) -> Select[Any]:
    """The fold ``usage_total_since`` executes (blizzard#517 D2) — one row of aggregates,
    modeled on ``analytics_operational_store.py``'s ``_spend_group_stmt``."""
    u = s.usage_facts
    stmt = select(*usage_aggregate_columns()).where(u.c.recorded_at >= since)
    if until is not None:
        stmt = stmt.where(u.c.recorded_at < until)
    return stmt


class ChunkUsageStore:
    """The chunk's token/cost usage facts."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def usage_total_since(self, since: datetime, *, until: datetime | None = None) -> UsageTotal:
        with self._store.read("usage_total_since") as conn:
            row = conn.execute(_usage_total_stmt(since, until)).one()
            return UsageTotal.of_grouped_sums(
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cache_read_tokens=row.cache_read_tokens,
                cache_create_tokens=row.cache_create_tokens,
                cost_usd_sum=row.cost_usd,
                null_cost_rows=row.null_cost_rows,
            )

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
