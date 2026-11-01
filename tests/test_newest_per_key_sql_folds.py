"""``bzh:newest-per-key-read`` — the reads that pick a key's newest row in SQL (component tier).

Each read answers the same keys at history depth 1 and depth N: the answer is the newest row, and
the rows the read fetches are equal at both depths. Beside the depth check, each read has the
equivalence case that a naive rewrite would get wrong."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert

from blizzard.foundation.clock import FixedClock
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_artifacts_store import ChunkArtifactsStore
from blizzard.hub.store.internal.chunk_decisions_store import ChunkDecisionsStore
from blizzard.hub.store.internal.chunk_escalations_store import ChunkEscalationsStore
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.support import count_rows_read, hub_store_connections, migrate_to, seed_chunk, seed_graph, stamp

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_SHALLOW, _DEEP = 1, 8


def _at(n: int) -> datetime:
    return _T0 + timedelta(days=n)


def _engine(tmp_path: Path, chunks: list[str]) -> Engine:
    tmp_path.mkdir(exist_ok=True)
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=_T0)
        for chunk_id in chunks:
            seed_chunk(conn, chunk_id, graph_id="gr_1", at=_T0)
    return engine


def _flat(tmp_path: Path, chunks: list[str], seed, read) -> None:
    """``seed(engine, depth)`` then ``read(engine)`` at both depths: equal answers and equal rows."""
    shallow, deep = _engine(tmp_path / "shallow", chunks), _engine(tmp_path / "deep", chunks)
    seed(shallow, _SHALLOW)
    seed(deep, _DEEP)
    assert read(shallow) == read(deep)
    assert count_rows_read(shallow, lambda: read(shallow)) == count_rows_read(deep, lambda: read(deep))


# --- decisions ------------------------------------------------------------------------------------


def _decision(conn, chunk_id: str, decision_id: str, *, at: datetime) -> None:
    conn.execute(
        insert(s.decisions).values(
            stamp(
                s.decisions,
                decision_id=decision_id,
                chunk_id=chunk_id,
                node_id="nd_1",
                node_name="n",
                epoch=1,
                choices=json.dumps([]),
                submitted_at=at,
            )
        )
    )


def _close(conn, chunk_id: str, decision_id: str) -> None:
    conn.execute(
        insert(s.escalations).values(
            stamp(s.escalations, chunk_id=chunk_id, epoch=1, decision_id=decision_id, recorded_at=_T0)
        )
    )


def _decisions(engine: Engine) -> ChunkDecisionsStore:
    return ChunkDecisionsStore(hub_store_connections(engine), FixedClock(_T0))


def test_live_decision_is_the_newest_not_closed_one_and_singular_equals_plural(tmp_path: Path) -> None:
    engine = _engine(tmp_path, ["ch_under", "ch_tie", "ch_none"])
    with engine.begin() as conn:
        _decision(conn, "ch_under", "dec_old_open", at=_at(1))
        _decision(conn, "ch_under", "dec_new_closed", at=_at(2))
        _close(conn, "ch_under", "dec_new_closed")
        _decision(conn, "ch_tie", "dec_a", at=_at(1))
        _decision(conn, "ch_tie", "dec_b", at=_at(1))
        _decision(conn, "ch_none", "dec_only_closed", at=_at(1))
        _close(conn, "ch_none", "dec_only_closed")
    store = _decisions(engine)

    plural = store.live_decisions_for(["ch_under", "ch_tie", "ch_none"])

    assert {chunk_id: live.decision_id for chunk_id, live in plural.items()} == {
        "ch_under": "dec_old_open",
        "ch_tie": "dec_b",
    }
    for chunk_id in ("ch_under", "ch_tie", "ch_none"):
        single = store.decision_for_chunk(chunk_id)
        assert (single.decision_id if single else None) == (
            plural[chunk_id].decision_id if chunk_id in plural else None
        )


def test_live_decisions_read_rows_flat_across_history_depth(tmp_path: Path) -> None:
    chunks = ["ch_1", "ch_2"]

    def seed(engine: Engine, depth: int) -> None:
        with engine.begin() as conn:
            for chunk_id in chunks:
                for n in range(depth):
                    _decision(conn, chunk_id, f"dec_{chunk_id}_{n}", at=_at(n))
                    _close(conn, chunk_id, f"dec_{chunk_id}_{n}")
                _decision(conn, chunk_id, f"dec_{chunk_id}_live", at=_at(depth))

    def read(engine: Engine) -> object:
        store = _decisions(engine)
        return store.live_decisions_for(chunks), [store.decision_for_chunk(c) is not None for c in chunks]

    _flat(tmp_path, chunks, seed, read)


# --- routes ---------------------------------------------------------------------------------------


def _route(conn, chunk_id: str, route_id: str, *, at: datetime, seq: int) -> None:
    conn.execute(
        insert(s.route_created).values(
            stamp(
                s.route_created,
                route_id=route_id,
                chunk_id=chunk_id,
                runner_id="r",
                workspace_id="w",
                created_at=at,
                seq=seq,
            )
        )
    )
    conn.execute(
        insert(s.route_environments).values(stamp(s.route_environments, route_id=route_id, environment_id="env"))
    )


def _release(conn, chunk_id: str, *, at: datetime, seq: int) -> None:
    conn.execute(insert(s.route_released).values(stamp(s.route_released, chunk_id=chunk_id, released_at=at, seq=seq)))


def test_route_newest_follows_timestamp_order_not_seq_when_the_clock_stepped_back(tmp_path: Path) -> None:
    engine = _engine(tmp_path, ["ch_1"])
    with engine.begin() as conn:
        _route(conn, "ch_1", "rt_later_clock", at=_at(5), seq=1)
        _route(conn, "ch_1", "rt_later_seq", at=_at(3), seq=2)
    routes = ChunkRouteStore(hub_store_connections(engine), FixedClock(_T0))

    assert routes.routes_for(["ch_1"])["ch_1"].route_id == "rt_later_clock"
    assert routes.load_all_routes()["ch_1"].route_id == "rt_later_clock"
    assert routes.route_of("ch_1").route_id == "rt_later_clock"  # type: ignore[union-attr]


def test_route_read_rows_flat_across_history_depth(tmp_path: Path) -> None:
    chunks = ["ch_1", "ch_2"]

    def seed(engine: Engine, depth: int) -> None:
        with engine.begin() as conn:
            for chunk_id in chunks:
                seq = 0
                for n in range(depth):
                    seq += 1
                    _route(conn, chunk_id, f"rt_{chunk_id}_{n}", at=_at(2 * n), seq=seq)
                    seq += 1
                    _release(conn, chunk_id, at=_at(2 * n + 1), seq=seq)
                _route(conn, chunk_id, f"rt_{chunk_id}_live", at=_at(2 * depth), seq=seq + 1)

    def read(engine: Engine) -> object:
        routes = ChunkRouteStore(hub_store_connections(engine), FixedClock(_T0))
        by_id = routes.routes_for(chunks)
        assert {r.route_id for r in by_id.values()} == {f"rt_{c}_live" for c in chunks}
        return sorted(by_id), routes.route_of("ch_1") is not None

    _flat(tmp_path, chunks, seed, read)


# --- escalations ----------------------------------------------------------------------------------


def _escalations(engine: Engine) -> ChunkEscalationsStore:
    return ChunkEscalationsStore(hub_store_connections(engine), FixedClock(_T0), facts=None)  # type: ignore[arg-type]


def _escalate(conn, chunk_id: str, *, at: datetime) -> None:
    conn.execute(insert(s.escalations).values(stamp(s.escalations, chunk_id=chunk_id, epoch=1, recorded_at=at)))


def test_escalation_candidates_drop_only_what_a_strictly_later_lease_or_requeue_supersedes(tmp_path: Path) -> None:
    chunks = ["ch_equal", "ch_lease", "ch_requeue", "ch_equal_requeue", "ch_bare"]
    engine = _engine(tmp_path, chunks)
    at = _at(3)
    with engine.begin() as conn:
        for chunk_id in chunks:
            _escalate(conn, chunk_id, at=at)
        for chunk_id, minted_at in (("ch_equal", at), ("ch_lease", at + timedelta(seconds=1))):
            conn.execute(
                insert(s.lease_facts).values(
                    stamp(s.lease_facts, chunk_id=chunk_id, epoch=1, runner_id="r", minted_at=minted_at)
                )
            )
        for chunk_id, requeued_at in (("ch_requeue", at + timedelta(seconds=1)), ("ch_equal_requeue", at)):
            conn.execute(insert(s.requeues).values(stamp(s.requeues, chunk_id=chunk_id, requeued_at=requeued_at)))

    assert set(_escalations(engine)._newest_escalation_per_chunk()) == {"ch_equal", "ch_equal_requeue", "ch_bare"}


def test_escalation_candidate_read_rows_flat_across_history_depth(tmp_path: Path) -> None:
    chunks = ["ch_1", "ch_2"]

    def seed(engine: Engine, depth: int) -> None:
        with engine.begin() as conn:
            for chunk_id in chunks:
                for n in range(depth):
                    _escalate(conn, chunk_id, at=_at(2 * n))
                    conn.execute(
                        insert(s.lease_facts).values(
                            stamp(s.lease_facts, chunk_id=chunk_id, epoch=n, runner_id="r", minted_at=_at(2 * n + 1))
                        )
                    )
                    conn.execute(
                        insert(s.requeues).values(stamp(s.requeues, chunk_id=chunk_id, requeued_at=_at(2 * n + 1)))
                    )
                _escalate(conn, chunk_id, at=_at(2 * depth))

    def read(engine: Engine) -> object:
        newest = _escalations(engine)._newest_escalation_per_chunk()
        assert set(newest) == set(chunks)
        return sorted(newest)

    _flat(tmp_path, chunks, seed, read)


# --- artifacts ------------------------------------------------------------------------------------


def test_latest_artifacts_read_rows_flat_across_history_depth(tmp_path: Path) -> None:
    names = ["plan", "report"]

    def seed(engine: Engine, depth: int) -> None:
        with engine.begin() as conn:
            for name in names:
                for n in range(depth):
                    conn.execute(
                        insert(s.artifacts).values(
                            stamp(
                                s.artifacts,
                                artifact_id=f"ar_{name}_{n}",
                                chunk_id="ch_1",
                                node_id="nd_1",
                                node_name="n",
                                epoch=1,
                                name=name,
                                kind="asset",
                                data="newest" if n == depth - 1 else f"old-{n}",
                                produced_at=_at(n),
                                seq=n,
                            )
                        )
                    )

    def read(engine: Engine) -> object:
        latest = ChunkArtifactsStore(hub_store_connections(engine), FixedClock(_T0)).latest_artifacts("ch_1", names)
        return {name: a.data for name, a in latest.items()}

    _flat(tmp_path, ["ch_1"], seed, read)


# --- graph enablement -----------------------------------------------------------------------------


def _mint(conn, graph_id: str, *, name: str, at: datetime) -> None:
    conn.execute(
        insert(s.graphs).values(
            stamp(s.graphs, graph_id=graph_id, name=name, entry_node_id="nd_1", definition_yaml="", created_at=at)
        )
    )


def _lifecycle(conn, graph_id: str, *, retired: bool) -> None:
    conn.execute(
        insert(s.graph_lifecycle_facts).values(
            stamp(s.graph_lifecycle_facts, graph_id=graph_id, retired=retired, set_at=_T0, set_by="op")
        )
    )


def test_enabled_by_name_is_the_newest_mint_whose_newest_lifecycle_fact_is_not_a_retirement(tmp_path: Path) -> None:
    engine = _engine(tmp_path, [])
    with engine.begin() as conn:
        _mint(conn, "gr_old", name="g", at=_at(1))
        _mint(conn, "gr_reenabled", name="g", at=_at(2))
        _lifecycle(conn, "gr_reenabled", retired=True)
        _lifecycle(conn, "gr_reenabled", retired=False)
        _mint(conn, "gr_retired", name="g", at=_at(3))
        _lifecycle(conn, "gr_retired", retired=True)
        _mint(conn, "gr_other", name="other", at=_at(4))
        _lifecycle(conn, "gr_other", retired=False)
        _mint(conn, "gr_all_retired", name="gone", at=_at(5))
        _lifecycle(conn, "gr_all_retired", retired=True)
    graphs = GraphStore(hub_store_connections(engine))

    assert graphs.graph_id_of_enabled_name("g") == "gr_reenabled"
    assert graphs.graph_id_of_enabled_name("gone") is None
    assert graphs.graph_id_of_enabled_name("missing") is None


def test_enabled_by_name_read_rows_flat_across_mint_depth(tmp_path: Path) -> None:
    def seed(engine: Engine, depth: int) -> None:
        with engine.begin() as conn:
            _mint(conn, "gr_live", name="g", at=_at(0))
            for n in range(depth):
                _mint(conn, f"gr_retired_{n}", name="g", at=_at(n + 1))
                _lifecycle(conn, f"gr_retired_{n}", retired=True)

    def read(engine: Engine) -> object:
        graphs = GraphStore(hub_store_connections(engine))
        assert graphs.graph_id_of_enabled_name("g") == "gr_live"
        return graphs.graph_id_of_enabled_name("g")

    _flat(tmp_path, [], seed, read)


# --- delivery landings ----------------------------------------------------------------------------


def _landed(conn, chunk_id: str, repo: str, commit: str, *, at: datetime) -> None:
    conn.execute(
        insert(s.delivery_repo_landed).values(
            stamp(s.delivery_repo_landed, chunk_id=chunk_id, repo=repo, commit_hash=commit, landed_at=at)
        )
    )


def test_legacy_landed_keeps_the_last_landing_per_chunk_and_repo(tmp_path: Path) -> None:
    engine = _engine(tmp_path, ["ch_1"])
    with engine.begin() as conn:
        _landed(conn, "ch_1", "a", "first", at=_at(1))
        _landed(conn, "ch_1", "b", "only", at=_at(2))
        _landed(conn, "ch_1", "a", "second", at=_at(3))
    artifacts = ChunkArtifactsStore(hub_store_connections(engine), FixedClock(_T0))

    assert artifacts.delivery_sources_for(["ch_1"])["ch_1"].legacy_landed == {"a": "second", "b": "only"}


def test_legacy_landed_read_rows_flat_across_history_depth(tmp_path: Path) -> None:
    chunks = ["ch_1", "ch_2"]

    def seed(engine: Engine, depth: int) -> None:
        with engine.begin() as conn:
            for chunk_id in chunks:
                for n in range(depth):
                    for repo in ("a", "b"):
                        _landed(conn, chunk_id, repo, f"c{n}", at=_at(n))

    def read(engine: Engine) -> object:
        artifacts = ChunkArtifactsStore(hub_store_connections(engine), FixedClock(_T0))
        return {c: sources.legacy_landed.keys() for c, sources in artifacts.delivery_sources_for(chunks).items()}

    shallow, deep = _engine(tmp_path / "shallow", chunks), _engine(tmp_path / "deep", chunks)
    seed(shallow, _SHALLOW)
    seed(deep, _DEEP)
    assert read(shallow) == read(deep)
    assert count_rows_read(shallow, lambda: read(shallow)) == count_rows_read(deep, lambda: read(deep))
