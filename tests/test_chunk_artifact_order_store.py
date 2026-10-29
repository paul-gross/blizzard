"""The artifacts store's write-order reads, driven over a head-migrated store (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert

from blizzard.foundation.clock import FixedClock
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_artifacts_store import ChunkArtifactsStore
from tests.support import hub_store_connections, migrate_to, seed_chunk, seed_graph, stamp

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _engine(tmp_path: Path) -> Engine:
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        seed_chunk(conn, "ch_a", graph_id="gr_1", at=_T0)
        seed_chunk(conn, "ch_b", graph_id="gr_1", at=_T0)
    return engine


def _artifact(engine: Engine, chunk_id: str, artifact_id: str, *, seq: int, name: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(s.artifacts).values(
                stamp(
                    s.artifacts,
                    artifact_id=artifact_id,
                    chunk_id=chunk_id,
                    node_id="nd_1",
                    node_name="n",
                    epoch=1,
                    name=name,
                    kind="asset",
                    data="d",
                    produced_at=_T0,
                    seq=seq,
                    forge=f"forge-{artifact_id}",
                )
            )
        )


def _seed_out_of_order(engine: Engine, *, prefix: str) -> None:
    """Insertion order and ``artifact_id`` order both disagree with ``seq``; ``ar_2``/``ar_9`` share a seq."""
    for artifact_id, seq in (("ar_1", 2), ("ar_9", 3), ("ar_3", 1), ("ar_2", 3)):
        _artifact(engine, "ch_a", artifact_id, seq=seq, name=f"{prefix}{artifact_id}")
    _artifact(engine, "ch_b", "ar_0", seq=0, name=f"{prefix}ar_0")


def _artifacts(engine: Engine) -> ChunkArtifactsStore:
    return ChunkArtifactsStore(hub_store_connections(engine), FixedClock(_T0))


def test_load_artifacts_returns_one_chunks_rows_in_seq_order_breaking_ties_by_artifact_id(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _seed_out_of_order(engine, prefix="")

    loaded = _artifacts(engine).load_artifacts("ch_a")

    assert [a.artifact_id for a in loaded] == ["ar_3", "ar_1", "ar_2", "ar_9"]
    assert [(a.chunk_id, a.forge) for a in loaded] == [("ch_a", f"forge-{a.artifact_id}") for a in loaded]


def test_delivery_sources_for_returns_each_chunks_markers_in_seq_order_breaking_ties_by_artifact_id(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_out_of_order(engine, prefix="merged/")

    sources = _artifacts(engine).delivery_sources_for(["ch_a", "ch_b"])

    assert [a.artifact_id for a in sources["ch_a"].markers] == ["ar_3", "ar_1", "ar_2", "ar_9"]
    assert [a.artifact_id for a in sources["ch_b"].markers] == ["ar_0"]
