"""Cross-graph target authoring (issue #90, Phase 2) — syntax, parse, reify, validate, mint warning.

Unit tier: ``to: graph:<name>`` parses into a structured target, validates without a
same-graph-node error, reifies onto the edge, and a malformed ``graph:`` form errors;
the optional per-choice ``model:`` override parses and reifies. Component tier: minting
a graph whose cross-graph target names an absent graph succeeds with a **warning** (late
binding), the edge round-trips through the store, and — the Phase 2→4 interim — selecting
a cross-graph choice before the apply-path lands falls through to a clean failure, never
a crash.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.graph import classify_choice_target, parse_graph_doc, target_graph_of
from blizzard.hub.domain.graph_authoring import reify_graph
from blizzard.hub.domain.graph_validation import validate_graph
from tests.support import build_hub, pointer_token, report_lease

unit = pytest.mark.unit
component = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_POINTER = {"source": "default", "ref": "9"}


def _doc(*, to: str, model: str | None = None) -> object:
    """A build graph whose ``pass`` choice routes to ``to`` (with an optional model)."""
    pass_choice: dict[str, object] = {"description": "done", "to": to}
    if model is not None:
        pass_choice["model"] = model
    return parse_graph_doc(
        {
            "name": "src",
            "entry": "build",
            "nodes": {
                "build": {
                    "executor": "runner",
                    "judgement": {
                        "prompt": "p",
                        "choices": {"pass": pass_choice, "fail": {"description": "retry", "to": "build"}},
                    },
                }
            },
        }
    )


# --------------------------------------------------------------------------- #
# Unit — the pure syntax parser
# --------------------------------------------------------------------------- #


@unit
def test_classify_choice_target_distinguishes_node_graph_and_malformed() -> None:
    assert classify_choice_target("review") == ("node", "review")
    assert classify_choice_target("done") == ("node", "done")
    assert classify_choice_target("graph:default-delivery") == ("graph", "default-delivery")
    assert classify_choice_target("graph:") == ("malformed", None)
    assert classify_choice_target("graph:a:b") == ("malformed", None)  # the deferred explicit-node form


# --------------------------------------------------------------------------- #
# Unit — parse / reify / validate
# --------------------------------------------------------------------------- #


@unit
def test_cross_graph_target_parses_reifies_and_validates() -> None:
    doc = _doc(to="graph:default-delivery")
    build = doc.node("build")
    pass_choice = next(c for c in build.judgement.choices if c.name == "pass")
    assert pass_choice.target_graph == "default-delivery"

    assert validate_graph(doc).ok  # no same-graph-node error for a well-formed cross-graph target

    graph = reify_graph(doc, FixedClock(_T0))
    edge = next(e for e in graph.edges if e.to_node_name == "graph:default-delivery")
    assert edge.target_graph == "default-delivery"
    # The target is recoverable from the raw persisted ``to_node_name`` alone.
    assert target_graph_of(edge.to_node_name) == "default-delivery"


@unit
def test_malformed_cross_graph_target_is_a_validation_error() -> None:
    for bad in ("graph:", "graph:a:b"):
        result = validate_graph(_doc(to=bad))
        assert not result.ok
        assert any("malformed cross-graph target" in e for e in result.errors), result.errors


@unit
def test_same_graph_to_a_nonexistent_sibling_still_errors() -> None:
    result = validate_graph(_doc(to="ghost"))
    assert not result.ok
    assert any("resolves to no node" in e for e in result.errors), result.errors


@unit
def test_per_choice_model_override_parses_and_reifies() -> None:
    doc = _doc(to="graph:default-delivery", model="claude-sonnet-5")
    build = doc.node("build")
    pass_choice = next(c for c in build.judgement.choices if c.name == "pass")
    assert pass_choice.model == "claude-sonnet-5"

    graph = reify_graph(doc, FixedClock(_T0))
    edge = next(e for e in graph.edges if e.target_graph == "default-delivery")
    assert edge.model == "claude-sonnet-5"


# --------------------------------------------------------------------------- #
# Component — mint warning, store round-trip, interim fall-through
# --------------------------------------------------------------------------- #

_CROSS_GRAPH_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: |
      Build the change.
    judgement:
      prompt: |
        Assess the build.
      choices:
        migrate:
          description: Hand off to the triage graph.
          to: graph:triage
          model: claude-sonnet-5
        fail:
          description: Retry.
          to: build
"""


@component
def test_minting_a_graph_with_an_unresolved_cross_graph_target_warns(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _CROSS_GRAPH_YAML})
    assert resp.status_code == 201, resp.text
    warnings = resp.json()["warnings"]
    # Late binding: the target `triage` is not minted yet, so it mints with a warning, not
    # an error (the two graphs may be authored in either order).
    assert any("triage" in w for w in warnings), warnings


@component
def test_cross_graph_edge_round_trips_through_the_store(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    doc = parse_graph_doc(
        {
            "name": "src",
            "entry": "build",
            "nodes": {
                "build": {
                    "executor": "runner",
                    "judgement": {
                        "prompt": "p",
                        "choices": {
                            "migrate": {"description": "go", "to": "graph:triage", "model": "claude-sonnet-5"},
                            "fail": {"description": "retry", "to": "build"},
                        },
                    },
                }
            },
        }
    )
    graph, _ = hub.services.graph_mint.mint(doc, definition_yaml="")

    loaded = hub.services.graphs.get(graph.graph_id)
    assert loaded is not None
    edge = next(e for e in loaded.edges if e.target_graph is not None)
    assert edge.target_graph == "triage"
    assert edge.to_node_name == "graph:triage"
    assert edge.model == "claude-sonnet-5"


@component
def test_interim_cross_graph_choice_falls_through_to_a_clean_failure(tmp_path: Path) -> None:
    """Phase 2→4 interim: authoring accepts a cross-graph choice, but the apply-path
    migration branch does not land until Phase 4. Selecting the choice must fall through
    to a safe failure — ``_resolve`` finds no same-graph node for ``graph:triage`` and
    ``apply`` returns its ``routes to unknown node`` rejection — never crash."""
    hub = build_hub(tmp_path)
    assert hub.client.post("/api/graphs", json={"definition_yaml": _CROSS_GRAPH_YAML}).status_code == 201
    chunk_id = hub.client.post("/api/chunks", json={"tokens": [pointer_token(_POINTER)]}).json()["chunk_id"]
    node_id = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["e"]},
    ).json()["envelope"]["node"]["node_id"]
    report_lease(hub, chunk_id, epoch=1, seq=1)

    resp = hub.client.post(
        f"/api/fleet/chunks/{chunk_id}/completions",
        json={"choice": "migrate", "epoch": 1, "runner_id": "r1", "from_node_id": node_id, "artifacts": []},
    )
    assert resp.status_code == 200
    assert resp.json()["outcome"] == "failure"
    # The chunk never advanced — no migration happened, and it stays claimable/running.
    assert hub.client.get(f"/api/chunks/{chunk_id}").json()["status"] == "running"
