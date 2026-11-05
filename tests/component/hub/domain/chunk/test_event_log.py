"""The operational event log, component tier: the escalation query count stays independent of fleet size.

``event_log`` is an append-only operational-fact table (``bzh:facts-not-status``): one
row per thing that happened, no status column."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from tests.support.migrations.event_log import T0
from tests.support.scaffolding import (
    chunk_stores,
    count_queries,
    migrate_to,
    seed_chunk,
    seed_graph,
)


def _at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_list_open_escalations_query_count_is_independent_of_fleet_size(tmp_path: Path) -> None:
    """``list_open_escalations``'s ``load_facts_for`` batch costs a bounded number of
    statements per read, not one more per candidate chunk as the fleet grows."""

    def _fleet(root: Path, n: int) -> tuple[ChunkStores, Engine]:
        root.mkdir()
        _, engine = migrate_to(root, "head")
        with engine.begin() as conn:
            seed_graph(conn, "gr_1", at=T0)
            for i in range(n):
                seed_chunk(conn, f"ch_{i}", graph_id="gr_1", at=T0)
        store = chunk_stores(engine, FixedClock(T0))
        for i in range(n):
            store.escalations.record_escalation(
                f"ch_{i}",
                epoch=1,
                takeover_command="cd a && resume",
                at=_at(10),
                admission=EpochAdmission.AT_OR_ABOVE,
                cause=None,
                detail=None,
            )
        return store, engine

    small, small_engine = _fleet(tmp_path / "small", 3)
    large, large_engine = _fleet(tmp_path / "large", 9)

    small_count = count_queries(small_engine, lambda: small.escalations.list_open_escalations())
    large_count = count_queries(large_engine, lambda: large.escalations.list_open_escalations())

    assert len(small.escalations.list_open_escalations()) == 3
    assert len(large.escalations.list_open_escalations()) == 9
    assert small_count == large_count
