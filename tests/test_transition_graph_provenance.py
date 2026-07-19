"""Transition graph-provenance (issue #90, Phase 1) — the read + hydration foundation.

Unit tier: :func:`blizzard.hub.api.chunks._history_views` resolves each transition's
node names against *its own* graph (``TransitionFact.graph_id``), so a two-graph history
never degrades an old-graph step to raw ``nd_`` ids against the new pin. Component tier:
:meth:`ChunkStore.load_facts` resolves each transition's ``to_node_executor`` against its
own graph — the silent ``RUNNER`` fallback the pre-#90 single-graph filter would hit.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import insert

from blizzard.foundation.clock import FixedClock
from blizzard.hub.api.chunks import _history_views
from blizzard.hub.domain.graph import Executor, Graph, parse_graph_doc
from blizzard.hub.domain.graph_authoring import reify_graph
from blizzard.hub.domain.work import ChunkFacts, TransitionFact
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
    doc = parse_graph_doc(
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
    return reify_graph(doc, FixedClock(_T0))


# --------------------------------------------------------------------------- #
# Unit — per-graph name resolution in the history view
# --------------------------------------------------------------------------- #


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

    views = _history_views(facts, {graph_a.graph_id: graph_a, graph_b.graph_id: graph_b})

    assert [(v.from_node_name, v.to_node_name) for v in views] == [("build", "review"), ("triage", "fix")]
    # No raw-id degradation: every name resolved against its own graph.
    assert all(v.from_node_name is not None and v.to_node_name is not None for v in views)


# --------------------------------------------------------------------------- #
# Component — per-graph executor hydration in load_facts
# --------------------------------------------------------------------------- #


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

    facts = hub.services.chunks.load_facts("ch_1")

    assert facts is not None
    by_target = {t.to_node_id: t for t in facts.transitions}
    # The gr_b hub node resolves to HUB — not the silent RUNNER fallback the single-graph
    # (gr_a-only) filter would have produced, since nd_hub lives in gr_b, not the pin.
    assert by_target["nd_hub"].to_node_executor is Executor.HUB
    assert by_target["nd_hub"].graph_id == "gr_b"
    assert by_target["nd_run"].to_node_executor is Executor.RUNNER
    assert by_target["nd_run"].graph_id == "gr_a"
