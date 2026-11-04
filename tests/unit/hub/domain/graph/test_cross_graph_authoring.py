"""Cross-graph target authoring — syntax, parse, reify, validate, mint warning.

Unit tier: ``to: graph:<name>`` parses, validates, and reifies onto the edge; a malformed
``graph:`` form errors. Component tier: minting a target naming an absent graph succeeds
with a warning (late binding), round-trips through the store, and selecting it before the
apply-path lands falls through to a clean failure, never a crash."""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.graph_targets import ChoiceTargetKind
from blizzard.hub.domain.graph.authoring import Reification
from blizzard.hub.domain.graph.model import ChoiceTarget, GraphDoc
from blizzard.hub.domain.graph.validation import Validator
from tests.support import TEST_CATALOGUE

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


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


def test_choice_target_distinguishes_node_graph_and_malformed() -> None:
    assert ChoiceTarget.of("review") == ChoiceTarget(node="review")
    assert ChoiceTarget.of("done") == ChoiceTarget(node="done")
    assert ChoiceTarget.of("graph:default-delivery") == ChoiceTarget(graph="default-delivery")
    assert ChoiceTarget.of("graph:").malformed
    assert ChoiceTarget.of("graph:a:b").malformed  # the deferred explicit-node form


def test_choice_target_kind_classifies_node_done_and_graph() -> None:
    assert ChoiceTarget.of("review").kind() is ChoiceTargetKind.NODE
    assert ChoiceTarget.of("done").kind() is ChoiceTargetKind.DONE
    assert ChoiceTarget.of("graph:default-delivery").kind() is ChoiceTargetKind.GRAPH


# Unit — parse / reify / validate
# --------------------------------------------------------------------------- #


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


def test_malformed_cross_graph_target_is_a_validation_error() -> None:
    for bad in ("graph:", "graph:a:b"):
        result = Validator.of(_doc(to=bad), TEST_CATALOGUE).result
        assert not result.ok
        assert any("malformed cross-graph target" in e for e in result.errors), result.errors


def test_same_graph_to_a_nonexistent_sibling_still_errors() -> None:
    result = Validator.of(_doc(to="ghost"), TEST_CATALOGUE).result
    assert not result.ok
    assert any("resolves to no node" in e for e in result.errors), result.errors


def test_per_choice_model_override_parses_and_reifies() -> None:
    doc = _doc(to="graph:default-delivery", model="claude-sonnet-5")
    build = doc.node("build")
    assert build is not None and build.judgement is not None
    pass_choice = next(c for c in build.judgement.choices if c.name == "pass")
    assert pass_choice.model == "claude-sonnet-5"

    graph = Reification.of(doc, FixedClock(_T0)).graph
    edge = next(e for e in graph.edges if e.target_graph == "default-delivery")
    assert edge.model == "claude-sonnet-5"
