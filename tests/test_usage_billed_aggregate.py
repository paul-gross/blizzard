"""The billed figure through the SQL usage aggregate (component tier): a group with a billed row
reads back that row's amount as ``billed_cost_usd``, and a group with none billed reads back
``None`` — the read both usage seams share through ``usage_aggregate_columns``."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.observability.analytics.operational import OperationalCriteria
from blizzard.hub.store.internal.analytics_operational_store import AnalyticsOperationalStore
from blizzard.hub.store.internal.chunk_usage_store import ChunkUsageStore
from tests.support import hub_store_connections, migrate_to, seed_chunk, seed_graph

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _stores(tmp_path: Path) -> tuple[ChunkUsageStore, AnalyticsOperationalStore]:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        for chunk_id in ("ch_billed", "ch_unbilled"):
            seed_chunk(conn, chunk_id, graph_id="gr_1", at=_T0)
    connections = hub_store_connections(engine)
    return ChunkUsageStore(connections, FixedClock(_T0)), AnalyticsOperationalStore(connections)


def _record(
    store: ChunkUsageStore, chunk_id: str, *, cost_usd: float | None, estimated_cost_usd: float | None = None
) -> None:
    store.record_usage(
        chunk_id,
        node_id="nd_build",
        epoch=1,
        runner_id="r1",
        kind="spawn",
        model="claude-opus-4-8",
        input_tokens=1,
        output_tokens=1,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=cost_usd,
        estimated_cost_usd=estimated_cost_usd,
        at=_T0,
    )


def test_a_group_reads_its_billed_rows_sum_and_a_group_with_none_billed_reads_none(tmp_path: Path) -> None:
    usage, analytics = _stores(tmp_path)
    _record(usage, "ch_billed", cost_usd=0.25)
    _record(usage, "ch_billed", cost_usd=0.5)
    _record(usage, "ch_billed", cost_usd=None, estimated_cost_usd=0.75)
    _record(usage, "ch_unbilled", cost_usd=None, estimated_cost_usd=0.75)

    page = analytics.spend_by_chunk(OperationalCriteria(), limit=10)

    totals = {r.key: r.total for r in page.records}
    assert totals["ch_billed"].billed_cost_usd == pytest.approx(0.75)
    assert totals["ch_billed"].billed_partial is True
    assert totals["ch_unbilled"].billed_cost_usd is None
    assert totals["ch_unbilled"].billed_partial is True


def test_the_windowed_total_reads_billed_the_same_way(tmp_path: Path) -> None:
    usage, _analytics = _stores(tmp_path)
    assert usage.usage_total_since(_T0).billed_cost_usd is None
    _record(usage, "ch_billed", cost_usd=0.25)

    assert usage.usage_total_since(_T0).billed_cost_usd == pytest.approx(0.25)
