"""The artifacts store's write-order reads and the hub-exec slot's acquire/release, each driven over a
head-migrated store (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert

from blizzard.foundation.clock import FixedClock
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_artifacts_store import ChunkArtifactsStore
from blizzard.hub.store.internal.chunk_hub_exec_store import ChunkHubExecStore
from tests.support import hub_store_connections, migrate_to, seed_chunk, seed_graph, stamp

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_STALE = timedelta(minutes=10)


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


def test_delivery_sources_for_returns_each_chunks_markers_in_seq_order_breaking_ties_by_artifact_id(
    tmp_path: Path,
) -> None:
    engine = _engine(tmp_path)
    _seed_out_of_order(engine, prefix="merged/")

    sources = _artifacts(engine).delivery_sources_for(["ch_a", "ch_b"])

    assert [a.artifact_id for a in sources["ch_a"].markers] == ["ar_3", "ar_1", "ar_2", "ar_9"]
    assert [a.artifact_id for a in sources["ch_b"].markers] == ["ar_0"]


def _slot(engine: Engine, slot_id: str, *, acquired_at: datetime) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(s.hub_exec_slot).values(
                stamp(
                    s.hub_exec_slot,
                    slot_id=slot_id,
                    holder_chunk_id="ch_a",
                    node_id="nd_1",
                    acquired_at=acquired_at,
                    released_at=None,
                )
            )
        )


def _released(engine: Engine) -> dict[str, datetime | None]:
    store = hub_store_connections(engine)
    with store.read("test") as conn:
        return {r.slot_id: r.released_at for r in conn.execute(store.select(s.hub_exec_slot))}


def _hub_exec(engine: Engine) -> ChunkHubExecStore:
    return ChunkHubExecStore(hub_store_connections(engine), FixedClock(_T0))


def test_acquire_defers_to_a_live_slot_one_second_short_of_stale(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _slot(engine, "hx_held", acquired_at=_T0)

    at = _T0 + _STALE - timedelta(seconds=1)
    assert _hub_exec(engine).acquire_hub_exec_slot("ch_b", node_id="nd_2", at=at, stale_after=_STALE) is None
    assert _released(engine) == {"hx_held": None}


@pytest.mark.parametrize("past_stale", [timedelta(0), timedelta(seconds=1)], ids=["at-stale", "past-stale"])
def test_acquire_reclaims_a_slot_held_stale_after_or_longer(tmp_path: Path, past_stale: timedelta) -> None:
    engine = _engine(tmp_path)
    _slot(engine, "hx_held", acquired_at=_T0)

    at = _T0 + _STALE + past_stale
    slot_id = _hub_exec(engine).acquire_hub_exec_slot("ch_b", node_id="nd_2", at=at, stale_after=_STALE)

    assert slot_id is not None
    assert _released(engine) == {"hx_held": at, slot_id: None}


def test_release_frees_only_the_named_slot_and_never_rewrites_a_released_one(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    _slot(engine, "hx_one", acquired_at=_T0)
    _slot(engine, "hx_two", acquired_at=_T0)
    store = _hub_exec(engine)
    first, second = _T0 + timedelta(seconds=5), _T0 + timedelta(seconds=9)

    store.release_hub_exec_slot("hx_one", at=first)
    store.release_hub_exec_slot("hx_one", at=second)

    assert _released(engine) == {"hx_one": first, "hx_two": None}
