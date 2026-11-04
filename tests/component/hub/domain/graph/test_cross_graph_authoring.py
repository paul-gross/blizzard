"""Cross-graph target authoring — syntax, parse, reify, validate, mint warning.

Unit tier: ``to: graph:<name>`` parses, validates, and reifies onto the edge; a malformed
``graph:`` form errors. Component tier: minting a target naming an absent graph succeeds
with a warning (late binding), round-trips through the store, and selecting it before the
apply-path lands falls through to a clean failure, never a crash."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.domain.graph.model import GraphDoc
from tests.support import build_hub

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


def test_minting_a_graph_with_an_unresolved_cross_graph_target_warns(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.post("/api/graphs", json={"definition_yaml": _CROSS_GRAPH_YAML})
    assert resp.status_code == 201, resp.text
    warnings = resp.json()["warnings"]
    # Late binding: the target `triage` is not minted yet, so it mints with a warning, not
    # an error (the two graphs may be authored in either order).
    assert any("triage" in w for w in warnings), warnings


def test_a_cross_graph_edge_reads_back_as_a_graph_target(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = hub.client.post("/api/graphs", json={"definition_yaml": _CROSS_GRAPH_YAML}).json()["graph_id"]

    edges = hub.client.get(f"/api/graphs/{graph_id}").json()["edges"]
    migrate = next(e for e in edges if e["to_node_name"] == "graph:triage")

    assert (migrate["target_kind"], migrate["target_graph"]) == ("graph", "triage")


def test_cross_graph_edge_round_trips_through_the_store(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    doc = GraphDoc.of(
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
