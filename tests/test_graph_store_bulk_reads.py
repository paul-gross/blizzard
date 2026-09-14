"""``GraphStore``'s narrow projections — ``load_graph_names``, ``load_node_names``,
``list_summaries``, and ``graph_id_of_enabled_name`` (component tier).

Each agrees with its fully-reified sibling (``get``/``get_enabled_by_name``/
``list_all``) across two minted graphs, one of them retired; ``load_graph_names`` and
``load_node_names`` are also proven correct across a lowered ``BATCH_SIZE`` boundary,
the ``tests/test_chunk_record_store_bulk_reads.py`` shape."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine

from blizzard.foundation.node_steps import Executor, JudgedBy, SessionMode
from blizzard.hub.domain.graph import Graph, Node
from blizzard.hub.store.internal import batching as batching_module
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.support import hub_store_connections, migrate_to

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _store(tmp_path: Path) -> tuple[GraphStore, Engine]:
    _, engine = migrate_to(tmp_path, "head")
    return GraphStore(hub_store_connections(engine)), engine


def _node(node_id: str, name: str, *, graph_id: str) -> Node:
    return Node(
        node_id=node_id,
        graph_id=graph_id,
        name=name,
        executor=Executor.RUNNER,
        prompt="p",
        checks=[],
        produces=[],
        session=SessionMode.FRESH,
        judged_by=JudgedBy.WORKER,
        retries_max=None,
        retries_exhausted=None,
    )


def _mint(
    store: GraphStore, graph_id: str, name: str, *, node_names: list[str] | None = None, created_at: datetime
) -> Graph:
    nodes = [_node(f"{graph_id}_nd_{i}", node_name, graph_id=graph_id) for i, node_name in enumerate(node_names or [])]
    graph = Graph(
        graph_id=graph_id,
        name=name,
        entry_node_id=nodes[0].node_id if nodes else "nd_missing",
        nodes=nodes,
        edges=[],
        created_at=created_at,
    )
    store.mint(graph, definition_yaml="", at=created_at)
    return graph


# --- load_graph_names --------------------------------------------------------- #


def test_load_graph_names_matches_get_across_two_graphs_including_a_retired_one(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    alpha = _mint(store, "gr_1", "alpha", created_at=_T0)
    beta = _mint(store, "gr_2", "beta", created_at=_T0)
    store.record_lifecycle(beta.graph_id, retired=True, at=_T0, by="op")

    result = store.load_graph_names([alpha.graph_id, beta.graph_id, "gr_never_minted"])

    # Graphs are immutable/insert-only — retirement does not exclude a graph here,
    # unlike the ephemeral-chunk exclusion `chunk_rows.graph_id_of_batch` performs.
    assert result == {alpha.graph_id: alpha.name, beta.graph_id: beta.name}
    for graph_id, name in result.items():
        loaded = store.get(graph_id)
        assert loaded is not None
        assert loaded.name == name


def test_load_graph_names_of_no_ids_is_empty(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    assert store.load_graph_names([]) == {}


def test_load_graph_names_matches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    graphs = [_mint(store, f"gr_batch_{i}", f"name_{i}", created_at=_T0) for i in range(7)]

    result = store.load_graph_names([g.graph_id for g in graphs])

    assert result == {g.graph_id: g.name for g in graphs}


# --- load_node_names ----------------------------------------------------------- #


def test_load_node_names_matches_node_by_id_across_two_graphs(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    alpha = _mint(store, "gr_1", "alpha", node_names=["build", "review"], created_at=_T0)
    beta = _mint(store, "gr_2", "beta", node_names=["deploy"], created_at=_T0)

    result = store.load_node_names([alpha.graph_id, beta.graph_id])

    assert result == {n.node_id: n.name for n in [*alpha.nodes, *beta.nodes]}
    loaded_alpha = store.get(alpha.graph_id)
    assert loaded_alpha is not None
    for node in alpha.nodes:
        found = loaded_alpha.node_by_id(node.node_id)
        assert found is not None
        assert found.name == result[node.node_id]


def test_load_node_names_of_no_ids_is_empty(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    assert store.load_node_names([]) == {}


def test_load_node_names_matches_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    store, _ = _store(tmp_path)
    graphs = [_mint(store, f"gr_batch_{i}", f"name_{i}", node_names=[f"node_{i}"], created_at=_T0) for i in range(7)]

    result = store.load_node_names([g.graph_id for g in graphs])

    assert result == {g.nodes[0].node_id: g.nodes[0].name for g in graphs}


# --- list_summaries ------------------------------------------------------------- #


def test_list_summaries_agrees_with_list_all_newest_first(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _mint(store, "gr_1", "alpha", created_at=_T0)
    _mint(store, "gr_2", "beta", created_at=_T0.replace(hour=1))

    summaries = store.list_summaries()
    graphs = store.list_all()

    assert [s.graph_id for s in summaries] == [g.graph_id for g in graphs] == ["gr_2", "gr_1"]
    for summary, graph in zip(summaries, graphs, strict=True):
        assert summary.graph_id == graph.graph_id
        assert summary.name == graph.name
        assert summary.entry_node_id == graph.entry_node_id
        assert summary.created_at == graph.created_at


# --- graph_id_of_enabled_name ---------------------------------------------------- #


def test_graph_id_of_enabled_name_matches_get_enabled_by_name(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    _mint(store, "gr_old", "shared", created_at=_T0)
    new = _mint(store, "gr_new", "shared", created_at=_T0.replace(hour=1))

    assert store.graph_id_of_enabled_name("shared") == new.graph_id
    enabled = store.get_enabled_by_name("shared")
    assert enabled is not None
    assert enabled.graph_id == new.graph_id


def test_graph_id_of_enabled_name_skips_a_retired_newest_mint_and_returns_the_next_newest_live_one(
    tmp_path: Path,
) -> None:
    store, _ = _store(tmp_path)
    old = _mint(store, "gr_old", "shared", created_at=_T0)
    new = _mint(store, "gr_new", "shared", created_at=_T0.replace(hour=1))
    store.record_lifecycle(new.graph_id, retired=True, at=_T0, by="op")

    assert store.graph_id_of_enabled_name("shared") == old.graph_id
    enabled = store.get_enabled_by_name("shared")
    assert enabled is not None
    assert enabled.graph_id == old.graph_id


def test_graph_id_of_enabled_name_is_none_when_every_mint_of_the_name_is_retired(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    only = _mint(store, "gr_1", "shared", created_at=_T0)
    store.record_lifecycle(only.graph_id, retired=True, at=_T0, by="op")

    assert store.graph_id_of_enabled_name("shared") is None
    assert store.get_enabled_by_name("shared") is None


def test_graph_id_of_enabled_name_is_none_for_a_name_never_minted(tmp_path: Path) -> None:
    store, _ = _store(tmp_path)
    assert store.graph_id_of_enabled_name("ghost") is None
