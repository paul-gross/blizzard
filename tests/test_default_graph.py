"""The packaged default graph (unit tier).

The hub ships a default graph every chunk pins at ingest (issue #229). Proves it
loads, inlines its prompt file references, and passes mint-time validation clean, and
that its single ``triage`` node routes every chunk out via a cross-graph migration or
straight to ``done``."""

from __future__ import annotations

import pytest

from blizzard.hub.domain.graph import Executor, SessionMode
from blizzard.hub.domain.graph_validation import validate_graph
from blizzard.hub.graphs import default_graph_yaml, load_default_graph_doc, packaged_graph_paths

pytestmark = pytest.mark.unit


def test_default_graph_validates_with_no_errors_or_warnings() -> None:
    result = validate_graph(load_default_graph_doc())
    assert result.ok, result.errors
    assert result.warnings == []


def test_default_graph_is_a_single_triage_router() -> None:
    doc = load_default_graph_doc()
    assert doc.name == "default-delivery"  # the ingest default pin resolves by this name
    assert doc.entry == "triage"
    assert [n.name for n in doc.nodes] == ["triage"]
    assert doc.node("triage").executor is Executor.RUNNER  # type: ignore[union-attr]


def test_default_graph_triage_routes_to_lanes_or_done() -> None:
    triage = load_default_graph_doc().node("triage")
    assert triage is not None and triage.judgement is not None
    routes = {c.name: c.to for c in triage.judgement.choices}
    assert routes == {
        "already-done": "done",
        "basic": "graph:bas-dwf",
        "advanced": "graph:adv-dwf",
        "harness": "graph:bas-hwf",
    }
    # The lane choices are cross-graph migration targets, parsed as such.
    targets = {c.name: c.target_graph for c in triage.judgement.choices}
    assert targets == {"already-done": None, "basic": "bas-dwf", "advanced": "adv-dwf", "harness": "bas-hwf"}


def test_default_graph_triage_is_a_fresh_advanced_tier_cold_read() -> None:
    doc = load_default_graph_doc()
    triage = doc.node("triage")
    assert triage is not None
    # Every entry is a fresh cold read: advanced tier preferred, frontier the fallback.
    assert triage.session is SessionMode.FRESH
    assert triage.session_source == "gate"
    assert doc.sessions["gate"].model == ["blizzard:advanced", "blizzard:frontier"]
    # The routing rationale rides out as the triage-findings asset.
    assert "triage-findings" in [p.name for p in triage.produces]


def test_default_graph_lane_targets_are_packaged_and_land_at_their_entries() -> None:
    # Every migration target ships in the same wheel, and none declares a `triage`
    # node — so a migration name-matches nothing and lands at the target's entry.
    from blizzard.hub.graphs import load_graph_doc

    docs = {doc.name: doc for doc in (load_graph_doc(p) for p in packaged_graph_paths())}
    assert {"bas-dwf", "adv-dwf", "bas-hwf"} <= set(docs)
    assert docs["bas-dwf"].entry == "build"
    assert docs["adv-dwf"].entry == "plan"
    assert docs["bas-hwf"].entry == "build"
    assert docs["bas-dwf"].node("triage") is None
    assert docs["adv-dwf"].node("triage") is None
    assert docs["bas-hwf"].node("triage") is None


def test_default_graph_reconciles_after_its_lane_targets() -> None:
    # `packaged_graph_paths` walks in directory order, so every lane target is minted
    # before the graph whose choices name them.
    order = [p.parent.name for p in packaged_graph_paths()]
    assert order.index("default") > order.index("basic-development-workflow")
    assert order.index("default") > order.index("advanced-development-workflow")
    assert order.index("default") > order.index("basic-harness-workflow")


def test_default_graph_prompts_are_inlined_not_paths() -> None:
    triage = load_default_graph_doc().node("triage")
    assert triage is not None
    assert triage.prompt is not None and not triage.prompt.startswith("./")
    assert triage.judgement is not None
    assert triage.judgement.prompt is not None and not triage.judgement.prompt.startswith("./")


def test_default_graph_yaml_is_readable_text() -> None:
    assert "entry: triage" in default_graph_yaml()
