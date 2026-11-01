"""SQLAlchemy adapter for the chunk usage seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). Facts only
(``bzh:facts-not-status``): every write appends a row; nothing here derives status.
Timestamps arrive already stamped (``bzh:injected-clock``)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Select

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunk.model import UsageTotal
from blizzard.hub.domain.chunk.ports.usage import IWriteChunkUsageRepository
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s

# Legacy usage reads share project-to-chunk reachability.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.project_reach import chunks_of_project

# Legacy usage reads share usage aggregation columns.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.usage_aggregate import usage_aggregate_columns
from blizzard.hub.store.scoped import ScopedConnections, ScopedStatements


def _usage_total_stmt(
    stmts: ScopedStatements, since: datetime, until: datetime | None, project: ProjectId | None = None
) -> Select[Any]:
    """The fold ``usage_total_since`` executes within ``stmts``' scope — one row of aggregates,
    modeled on ``src/blizzard/hub/store/internal/analytics_operational_store.py``'s ``_spend_group_stmt``."""
    u = s.usage_facts
    stmt = stmts.select(u, *usage_aggregate_columns()).where(u.c.recorded_at >= since)
    if until is not None:
        stmt = stmt.where(u.c.recorded_at < until)
    if project is not None:
        stmt = stmt.where(u.c.chunk_id.in_(chunks_of_project(stmts, project)))
    return stmt


class ChunkUsageStore:
    """The chunk's token/cost usage facts."""

    def __init__(self, store: ScopedConnections, clock: IClock) -> None:
        self._store = store
        self._clock = clock

    def usage_total_since(
        self, since: datetime, *, until: datetime | None = None, project: ProjectId | None = None
    ) -> UsageTotal:
        with self._store.read("usage_total_since") as conn:
            row = conn.execute(_usage_total_stmt(self._store, since, until, project=project)).one()
            return UsageTotal.of_grouped_sums(
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                cache_read_tokens=row.cache_read_tokens,
                cache_create_tokens=row.cache_create_tokens,
                cost_usd_sum=row.cost_usd,
                estimated_cost_usd_sum=row.estimated_cost_usd,
                estimated_rows=row.estimated_rows,
                both_null_rows=row.both_null_rows,
                null_cost_rows=row.null_cost_rows,
                billed_rows=row.billed_rows,
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
        harness_id: str | None = None,
        harness_version: str | None = None,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_create_tokens: int,
        cost_usd: float | None,
        estimated_cost_usd: float | None = None,
        at: datetime,
    ) -> None:
        with self._store.write("record_usage") as conn:
            conn.execute(
                self._store.insert(s.usage_facts).values(
                    chunk_id=chunk_id,
                    node_id=node_id,
                    epoch=epoch,
                    runner_id=runner_id,
                    kind=kind,
                    model=model,
                    harness_id=harness_id,
                    harness_version=harness_version,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_read_tokens=cache_read_tokens,
                    cache_create_tokens=cache_create_tokens,
                    cost_usd=cost_usd,
                    estimated_cost_usd=estimated_cost_usd,
                    recorded_at=at,
                )
            )


_usage_conforms: type[IWriteChunkUsageRepository] = ChunkUsageStore
