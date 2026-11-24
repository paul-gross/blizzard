"""The operational event log, component tier: the escalation query count stays independent of fleet size.

``event_log`` is an append-only operational-fact table (``bzh:facts-not-status``): one
row per thing that happened, no status column."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import Engine, text

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.ports.stores import ChunkStores
from tests.support.migrations.event_log import T0
from tests.support.scaffolding import (
    capture_statements,
    chunk_stores,
    chunk_unit,
    count_queries,
    hub_store_connections,
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
            with chunk_unit(hub_store_connections(engine), f"ch_{i}") as unit:
                store.escalations.record_escalation(
                    f"ch_{i}",
                    epoch=1,
                    takeover_command="cd a && resume",
                    at=_at(10),
                    admission=EpochAdmission.AT_OR_ABOVE,
                    terminal=False,
                    cause=None,
                    detail=None,
                    transaction=unit,
                )
        return store, engine

    small, small_engine = _fleet(tmp_path / "small", 3)
    large, large_engine = _fleet(tmp_path / "large", 9)

    small_count = count_queries(small_engine, lambda: small.escalations.list_open_escalations())
    large_count = count_queries(large_engine, lambda: large.escalations.list_open_escalations())

    assert len(small.escalations.list_open_escalations()) == 3
    assert len(large.escalations.list_open_escalations()) == 9
    assert small_count == large_count


def test_a_write_carrying_three_artifacts_reads_seq_once_and_keeps_their_order(tmp_path: Path) -> None:
    """The artifact insert reads ``max(seq)`` once per write and inserts every row in one
    executemany: two artifact statements for three artifacts, with ``seq`` following input order."""
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=T0)
    store = chunk_stores(engine, FixedClock(T0))
    rows = [
        StoredArtifact(
            kind=ArtifactKind.ASSET,
            name=f"a{i}",
            data=str(i),
            repo=None,
            forge=None,
            artifact_id=f"art_{i}",
            chunk_id="ch_1",
            node_id="nd_1",
            node_name="build",
            epoch=1,
        )
        for i in (2, 0, 1)
    ]
    with capture_statements(engine) as statements, chunk_unit(hub_store_connections(engine), "ch_1") as unit:
        store.escalations.record_escalation(
            "ch_1",
            epoch=1,
            takeover_command="cd a && resume",
            at=_at(10),
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=False,
            cause=None,
            detail=None,
            artifacts=rows,
            transaction=unit,
        )
    assert len([sql for sql, _ in statements if "artifacts" in sql]) == 2
    with engine.connect() as conn:
        written = conn.execute(text("SELECT name, seq FROM artifacts ORDER BY seq")).all()
    assert [name for name, _ in written] == ["a2", "a0", "a1"]
    assert [seq for _, seq in written] == [1, 2, 3]


def test_list_open_escalations_loads_the_status_families_only(tmp_path: Path) -> None:
    """The open-escalation read stays within the narrowed status load: at most 22 statements."""
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=T0)
        seed_chunk(conn, "ch_1", graph_id="gr_1", at=T0)
    store = chunk_stores(engine, FixedClock(T0))
    with chunk_unit(hub_store_connections(engine), "ch_1") as unit:
        store.escalations.record_escalation(
            "ch_1",
            epoch=1,
            takeover_command="cd a && resume",
            at=_at(10),
            admission=EpochAdmission.AT_OR_ABOVE,
            terminal=False,
            cause=None,
            detail=None,
            transaction=unit,
        )
    assert len(store.escalations.list_open_escalations()) == 1
    assert count_queries(engine, lambda: store.escalations.list_open_escalations()) <= 22
