"""Graph reification (unit tier) — the doc -> immutable graph compile."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Executor, JudgedBy
from blizzard.hub.domain.graph_authoring import reify_graph
from blizzard.hub.graphs import load_default_graph_doc

pytestmark = pytest.mark.unit


def _clock() -> FixedClock:
    return FixedClock(datetime(2026, 7, 13, tzinfo=UTC))


def test_reify_mints_ids_and_splits_choices_into_edges() -> None:
    doc = load_default_graph_doc()
    graph = reify_graph(doc, _clock())

    assert graph.graph_id.startswith("gr_")
    build = graph.node_by_name("build")
    deliver = graph.node_by_name("deliver")
    assert build is not None and deliver is not None
    assert graph.entry_node_id == build.node_id
    assert build.executor is Executor.RUNNER
    assert deliver.executor is Executor.HUB
    assert build.judged_by is JudgedBy.WORKER

    # build's two fused choices reify into two choices and two edges (pass -> review,
    # fail -> build) in the P7 build -> review -> deliver default graph.
    assert {c.name for c in build.choices} == {"pass", "fail"}
    assert all(c.choice_id.startswith("cho_") for c in build.choices)
    targets = {e.to_node_name for e in graph.edges_from(build.node_id)}
    assert targets == {"review", "build"}

    # The deliver hub node authors no judgement, so it carries no edges — its
    # machinery outcomes (landed/conflict) are applied by the coordinator.
    assert deliver.choices == []
    assert graph.edges_from(deliver.node_id) == []


def test_reify_preserves_judgement_prompt_and_addendum() -> None:
    doc = load_default_graph_doc()
    graph = reify_graph(doc, _clock())
    build = graph.node_by_name("build")
    assert build is not None
    assert build.judgement_prompt  # inlined by the loader, carried onto the node
    fail_edge = next(e for e in graph.edges_from(build.node_id) if e.to_node_name == "build")
    assert fail_edge.prompt_addendum  # the fail -> build arrival addendum


def test_edge_for_choice_resolves_by_name() -> None:
    graph = reify_graph(load_default_graph_doc(), _clock())
    build = graph.node_by_name("build")
    assert build is not None
    edge = graph.edge_for_choice(build.node_id, "pass")
    assert edge is not None and edge.to_node_name == "review"
    assert graph.edge_for_choice(build.node_id, "nonexistent") is None
    assert RESERVED_TERMINAL not in {n.name for n in graph.nodes}
