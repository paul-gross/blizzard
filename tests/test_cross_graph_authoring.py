"""Cross-graph target authoring — syntax, parse, reify, validate, mint warning.

Unit tier: ``to: graph:<name>`` parses, validates, and reifies onto the edge; a malformed
``graph:`` form errors. Component tier: minting a target naming an absent graph succeeds
with a warning (late binding), round-trips through the store, and selecting it before the
apply-path lands falls through to a clean failure, never a crash."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import TimeoutError as PoolTimeout

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.graph_targets import ChoiceTargetKind
from blizzard.hub.api.chunks import ingest_chunk
from blizzard.hub.domain.graph.authoring import Reification
from blizzard.hub.domain.graph.model import ChoiceTarget, GraphDoc
from blizzard.hub.domain.graph.validation import Validator
from blizzard.wire.chunk import ChunkIngestRequest, ChunkIngestResponse
from tests import support
from tests.support import TEST_CATALOGUE, build_hub

unit = pytest.mark.unit
component = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_POINTER = {"source": "default", "ref": "9"}


@component
def test_first_ingest_with_cross_graph_default_uses_one_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def constrained_engine(url: str) -> sa.Engine:
        return sa.create_engine(
            url, connect_args={"check_same_thread": False}, pool_size=1, max_overflow=0, pool_timeout=0.5
        )

    monkeypatch.setattr(support, "create_engine_from_url", constrained_engine)
    hub = build_hub(tmp_path)
    doc = _doc(to="graph:delivery")
    definition = """\
name: src
entry: build
nodes:
  build:
    executor: runner
    judgement:
      prompt: p
      choices:
        pass: {description: done, to: 'graph:delivery'}
        fail: {description: retry, to: build}
"""
    services = replace(hub.services, default_graph_doc=doc, default_graph_yaml=definition)
    try:
        with hub.engine.connect(), pytest.raises(PoolTimeout):
            hub.engine.connect()
        result = ingest_chunk(ChunkIngestRequest(tokens=["default:9"]), services)
        assert isinstance(result, ChunkIngestResponse)
        chunk = services.chunks.record.get(result.chunk_id)
        assert chunk is not None
        graph = services.graphs.get(chunk.graph_id)
        assert graph is not None and graph.cross_graph_targets() == ["delivery"]
        assert services.graphs.get_enabled_by_name(doc.name) == graph
    finally:
        hub.engine.dispose()


def _doc(*, to: str, model: str | None = None) -> GraphDoc:
    """A build graph whose ``pass`` choice routes to ``to`` (with an optional model)."""
    pass_choice: dict[str, object] = {"description": "done", "to": to}
    if model is not None:
        pass_choice["model"] = model
    return GraphDoc.of(
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


# Unit — the pure syntax parser
# --------------------------------------------------------------------------- #


@unit
def test_choice_target_distinguishes_node_graph_and_malformed() -> None:
    assert ChoiceTarget.of("review") == ChoiceTarget(node="review")
    assert ChoiceTarget.of("done") == ChoiceTarget(node="done")
    assert ChoiceTarget.of("graph:default-delivery") == ChoiceTarget(graph="default-delivery")
    assert ChoiceTarget.of("graph:").malformed
    assert ChoiceTarget.of("graph:a:b").malformed  # the deferred explicit-node form


@unit
def test_choice_target_kind_classifies_node_done_and_graph() -> None:
    assert ChoiceTarget.of("review").kind() is ChoiceTargetKind.NODE
    assert ChoiceTarget.of("done").kind() is ChoiceTargetKind.DONE
    assert ChoiceTarget.of("graph:default-delivery").kind() is ChoiceTargetKind.GRAPH


# Unit — parse / reify / validate
# --------------------------------------------------------------------------- #


@unit
def test_cross_graph_target_parses_reifies_and_validates() -> None:
    doc = _doc(to="graph:default-delivery")
    build = doc.node("build")
    assert build is not None and build.judgement is not None
    pass_choice = next(c for c in build.judgement.choices if c.name == "pass")
    assert pass_choice.target_graph == "default-delivery"

    assert Validator.of(doc, TEST_CATALOGUE).result.ok  # no same-graph-node error for a well-formed cross-graph target

    graph = Reification.of(doc, FixedClock(_T0)).graph
    edge = next(e for e in graph.edges if e.to_node_name == "graph:default-delivery")
    assert edge.target_graph == "default-delivery"
    # The target is recoverable from the raw persisted ``to_node_name`` alone.
    assert ChoiceTarget.of(edge.to_node_name).graph == "default-delivery"


@unit
def test_malformed_cross_graph_target_is_a_validation_error() -> None:
    for bad in ("graph:", "graph:a:b"):
        result = Validator.of(_doc(to=bad), TEST_CATALOGUE).result
        assert not result.ok
        assert any("malformed cross-graph target" in e for e in result.errors), result.errors


@unit
def test_same_graph_to_a_nonexistent_sibling_still_errors() -> None:
    result = Validator.of(_doc(to="ghost"), TEST_CATALOGUE).result
    assert not result.ok
    assert any("resolves to no node" in e for e in result.errors), result.errors


@unit
def test_per_choice_model_override_parses_and_reifies() -> None:
    doc = _doc(to="graph:default-delivery", model="claude-sonnet-5")
    build = doc.node("build")
    assert build is not None and build.judgement is not None
    pass_choice = next(c for c in build.judgement.choices if c.name == "pass")
    assert pass_choice.model == "claude-sonnet-5"

    graph = Reification.of(doc, FixedClock(_T0)).graph
    edge = next(e for e in graph.edges if e.target_graph == "default-delivery")
    assert edge.model == "claude-sonnet-5"


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
def test_a_cross_graph_edge_reads_back_as_a_graph_target(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    graph_id = hub.client.post("/api/graphs", json={"definition_yaml": _CROSS_GRAPH_YAML}).json()["graph_id"]

    edges = hub.client.get(f"/api/graphs/{graph_id}").json()["edges"]
    migrate = next(e for e in edges if e["to_node_name"] == "graph:triage")

    assert (migrate["target_kind"], migrate["target_graph"]) == ("graph", "triage")


@component
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
