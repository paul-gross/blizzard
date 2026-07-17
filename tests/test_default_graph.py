"""The packaged default graph (unit tier).

The hub ships a default graph every chunk pins at ingest. This proves it
loads, inlines its prompt file references, and passes mint-time validation
clean — so a fresh hub's ``POST /graphs`` of it can never be rejected. P7 promoted it
from the walking-skeleton ``build -> deliver`` to the full ``build -> review ->
deliver`` MVP shape with the review-fail cycle.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.graph import Executor, SessionMode
from blizzard.hub.domain.graph_validation import validate_graph
from blizzard.hub.graphs import default_graph_yaml, load_default_graph_doc

pytestmark = pytest.mark.unit


def test_default_graph_validates_with_no_errors_or_warnings() -> None:
    result = validate_graph(load_default_graph_doc())
    assert result.ok, result.errors
    assert result.warnings == []


def test_default_graph_is_build_review_deliver() -> None:
    doc = load_default_graph_doc()
    assert doc.entry == "build"
    names = {n.name for n in doc.nodes}
    assert names == {"build", "review", "deliver"}
    assert doc.node("deliver").executor is Executor.HUB  # type: ignore[union-attr]
    assert doc.node("review").executor is Executor.RUNNER  # type: ignore[union-attr]


def test_default_graph_build_passes_to_review() -> None:
    build = load_default_graph_doc().node("build")
    assert build is not None and build.judgement is not None
    routes = {c.name: c.to for c in build.judgement.choices}
    assert routes == {"pass": "review", "fail": "build"}


def test_default_graph_review_is_cold_eyes_and_loops_to_build() -> None:
    review = load_default_graph_doc().node("review")
    assert review is not None and review.judgement is not None
    # Cold eyes and it emits the findings asset the fail edge carries back.
    assert review.session is SessionMode.FRESH
    assert "review-findings" in review.produces
    routes = {c.name: c.to for c in review.judgement.choices}
    assert routes == {"pass": "deliver", "fail": "build"}
    # The fail edge carries an inlined arrival addendum into build.
    fail = next(c for c in review.judgement.choices if c.name == "fail")
    assert fail.prompt_addendum is not None and not fail.prompt_addendum.startswith("./")


def test_default_graph_prompts_are_inlined_not_paths() -> None:
    doc = load_default_graph_doc()
    for node_name in ("build", "review"):
        node = doc.node(node_name)
        assert node is not None
        assert node.prompt is not None and not node.prompt.startswith("./")
        assert node.judgement is not None
        assert node.judgement.prompt is not None and not node.judgement.prompt.startswith("./")


def test_default_graph_yaml_is_readable_text() -> None:
    assert "entry: build" in default_graph_yaml()
