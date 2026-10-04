"""Transition graph-provenance — the read + hydration foundation.

Unit tier: :meth:`ChunkHistoryView.transitions` resolves each transition's node names
against *its own* graph, so a two-graph history never degrades an old-graph step to raw ``nd_`` ids.
Component tier: :meth:`ChunkFactsStore.load_facts` resolves ``to_node_executor`` the same way.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.node_steps import Executor
from blizzard.hub.api.chunk_views import ChunkHistoryView
from blizzard.hub.api.graph_names import GraphNames
from blizzard.hub.domain.chunk.model import ChunkFacts, MigrationFact, MigrationSource, TransitionFact
from blizzard.hub.domain.graph.authoring import Reification
from blizzard.hub.domain.graph.model import Graph, GraphDoc, GraphSummary
from blizzard.hub.store import schema as s
from tests.support import build_hub

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _two_node_graph(entry: str, other: str, *, other_executor: str) -> Graph:
    """A minimal two-node graph: a worker ``entry`` routing to ``other``."""
    other_body: dict[str, object] = (
        {
            "executor": "hub",
            "run": [{"command": "c", "produces": "m"}],
            "judgement": {"choices": {"ok": {"description": "d", "to": "done"}}},
        }
        if other_executor == "hub"
        else {"executor": "runner", "judgement": {"prompt": "p", "choices": {"ok": {"description": "d", "to": "done"}}}}
    )
    doc = GraphDoc.of(
        {
            "name": f"g-{entry}",
            "entry": entry,
            "nodes": {
                entry: {
                    "executor": "runner",
                    "judgement": {"prompt": "p", "choices": {"go": {"description": "d", "to": other}}},
                },
                other: other_body,
            },
        }
    )
    return Reification.of(doc, FixedClock(_T0)).graph


class _GraphLookup:
    """A minimal ``IReadGraphRepository`` stand-in over an in-memory ``{graph_id: Graph}``
    map — only the two narrow projections ``GraphNames`` itself reaches."""

    def __init__(self, by_id: dict[str, Graph]) -> None:
        self._by_id = by_id

    def load_graph_summaries(self, graph_ids):  # type: ignore[no-untyped-def]
        return {
            graph_id: GraphSummary(
                graph_id=graph.graph_id, name=graph.name, entry_node_id=graph.entry_node_id, created_at=graph.created_at
            )
            for graph_id in graph_ids
            if (graph := self._by_id.get(graph_id)) is not None
        }

    def load_node_names(self, graph_ids):  # type: ignore[no-untyped-def]
        return {
            graph_id: {node.node_id: node.name for node in graph.nodes}
            for graph_id in graph_ids
            if (graph := self._by_id.get(graph_id)) is not None
        }


# Unit — per-graph name resolution in the history view


def test_history_view_resolves_each_step_name_against_its_own_graph() -> None:
    graph_a = _two_node_graph("build", "review", other_executor="runner")
    graph_b = _two_node_graph("triage", "fix", other_executor="runner")
    a_build = graph_a.node_by_name("build")
    a_review = graph_a.node_by_name("review")
    b_triage = graph_b.node_by_name("triage")
    b_fix = graph_b.node_by_name("fix")
    assert a_build is not None and a_review is not None and b_triage is not None and b_fix is not None

    facts = ChunkFacts(
        minted=True,
        transitions=[
            TransitionFact(
                to_node_id=a_review.node_id,
                to_node_executor=Executor.RUNNER,
                epoch=1,
                recorded_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
                from_node_id=a_build.node_id,
                choice_name="go",
                graph_id=graph_a.graph_id,
            ),
            TransitionFact(
                to_node_id=b_fix.node_id,
                to_node_executor=Executor.RUNNER,
                epoch=2,
                recorded_at=datetime(2026, 1, 1, 2, tzinfo=UTC),
                from_node_id=b_triage.node_id,
                choice_name="go",
                graph_id=graph_b.graph_id,
            ),
        ],
    )

    by_id = {graph_a.graph_id: graph_a, graph_b.graph_id: graph_b}
    views = ChunkHistoryView(facts, GraphNames(_GraphLookup(by_id))).transitions()  # type: ignore[arg-type]

    assert [(v.from_node_name, v.to_node_name) for v in views] == [("build", "review"), ("triage", "fix")]
    # No raw-id degradation: every name resolved against its own graph.
    assert all(v.from_node_name is not None and v.to_node_name is not None for v in views)


def test_migration_view_projects_both_graphs_and_orders_tied_instants_by_epoch() -> None:
    source = _two_node_graph("build", "review", other_executor="runner")
    target = _two_node_graph("triage", "fix", other_executor="runner")
    build = source.node_by_name("build")
    review = source.node_by_name("review")
    triage = target.node_by_name("triage")
    fix = target.node_by_name("fix")
    assert build is not None and review is not None and triage is not None and fix is not None
    tied_at = datetime(2026, 1, 1, 1, tzinfo=UTC)
    later_at = datetime(2026, 1, 1, 2, tzinfo=UTC)
    facts = ChunkFacts(
        minted=True,
        migrations=[
            MigrationFact(
                from_node_id=review.node_id,
                from_graph_id=source.graph_id,
                to_graph_id=target.graph_id,
                landed_node_id=fix.node_id,
                choice_name=None,
                model=None,
                epoch=1,
                recorded_at=later_at,
                source=MigrationSource.FOLLOW_LATEST,
            ),
            MigrationFact(
                from_node_id=None,
                from_graph_id=target.graph_id,
                to_graph_id=source.graph_id,
                landed_node_id=review.node_id,
                choice_name=None,
                model=None,
                epoch=4,
                recorded_at=tied_at,
                source=None,
            ),
            MigrationFact(
                from_node_id=build.node_id,
                from_graph_id=source.graph_id,
                to_graph_id=target.graph_id,
                landed_node_id=triage.node_id,
                choice_name="basic",
                model="selected-model",
                epoch=2,
                recorded_at=tied_at,
                source=MigrationSource.AUTHORED_EDGE,
            ),
        ],
    )
    names = GraphNames(_GraphLookup({source.graph_id: source, target.graph_id: target}))  # type: ignore[arg-type]

    views = ChunkHistoryView(facts, names).migrations()

    assert [v.epoch for v in views] == [2, 4, 1]
    assert views[0].model_dump() == {
        "from_node_id": build.node_id,
        "from_node_name": "build",
        "from_graph_id": source.graph_id,
        "from_graph_name": source.name,
        "to_graph_id": target.graph_id,
        "to_graph_name": target.name,
        "landed_node_id": triage.node_id,
        "landed_node_name": "triage",
        "choice_name": "basic",
        "model": "selected-model",
        "source": "authored-edge",
        "epoch": 2,
        "recorded_at": tied_at.isoformat(),
    }
    assert views[1].model_dump() == {
        "from_node_id": None,
        "from_node_name": None,
        "from_graph_id": target.graph_id,
        "from_graph_name": target.name,
        "to_graph_id": source.graph_id,
        "to_graph_name": source.name,
        "landed_node_id": review.node_id,
        "landed_node_name": "review",
        "choice_name": None,
        "model": None,
        "source": None,
        "epoch": 4,
        "recorded_at": tied_at.isoformat(),
    }
    assert (views[2].from_node_name, views[2].landed_node_name, views[2].source, views[2].recorded_at) == (
        "review",
        "fix",
        "follow-latest",
        later_at.isoformat(),
    )


# Component — per-graph executor hydration in load_facts


def test_load_facts_resolves_each_transition_executor_against_its_own_graph(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    engine = hub.engine
    with engine.begin() as conn:
        # Two graphs; a runner node in gr_a and a hub node in gr_b.
        for gid in ("gr_a", "gr_b"):
            conn.execute(
                insert(s.graphs).values(
                    graph_id=gid, name=gid, entry_node_id="nd_run", definition_yaml="", created_at=_T0
                )
            )
        conn.execute(
            insert(s.graph_nodes).values(
                node_id="nd_run", graph_id="gr_a", name="build", executor="runner", session="resume", judged_by="worker"
            )
        )
        conn.execute(
            insert(s.graph_nodes).values(
                node_id="nd_hub", graph_id="gr_b", name="merge", executor="hub", session="resume", judged_by="worker"
            )
        )
        conn.execute(insert(s.chunks).values(chunk_id="ch_1", graph_id="gr_a", minted_at=_T0, model="m"))
        # A history spanning both graphs: the second step targets gr_b's hub node.
        conn.execute(
            insert(s.transitions).values(
                transition_id="tr_1",
                chunk_id="ch_1",
                graph_id="gr_a",
                from_node_id=None,
                to_node_id="nd_run",
                choice_name=None,
                epoch=1,
                runner_id="r",
                recorded_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
            )
        )
        conn.execute(
            insert(s.transitions).values(
                transition_id="tr_2",
                chunk_id="ch_1",
                graph_id="gr_b",
                from_node_id="nd_run",
                to_node_id="nd_hub",
                choice_name="go",
                epoch=1,
                runner_id="r",
                recorded_at=datetime(2026, 1, 1, 2, tzinfo=UTC),
            )
        )

    facts = hub.services.chunks.facts.load_facts("ch_1")

    assert facts is not None
    by_target = {t.to_node_id: t for t in facts.transitions}
    # The gr_b hub node resolves to HUB, not a raw RUNNER fallback — nd_hub lives in
    # gr_b, not the pin.
    assert by_target["nd_hub"].to_node_executor is Executor.HUB
    assert by_target["nd_hub"].graph_id == "gr_b"
    assert by_target["nd_run"].to_node_executor is Executor.RUNNER
    assert by_target["nd_run"].graph_id == "gr_a"
