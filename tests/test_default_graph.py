"""The packaged default graph (unit tier) — D-081.

The hub ships a default graph every chunk pins at ingest (D-081). This proves it
loads, inlines its prompt file references (D-033), and passes mint-time validation
clean — so a fresh hub's ``POST /graphs`` of it can never be rejected.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.graph import Executor
from blizzard.hub.domain.graph_validation import validate_graph
from blizzard.hub.graphs import default_graph_yaml, load_default_graph_doc

pytestmark = pytest.mark.unit


def test_default_graph_validates_with_no_errors_or_warnings() -> None:
    result = validate_graph(load_default_graph_doc())
    assert result.ok, result.errors
    assert result.warnings == []


def test_default_graph_is_build_then_deliver() -> None:
    doc = load_default_graph_doc()
    assert doc.entry == "build"
    names = {n.name for n in doc.nodes}
    assert names == {"build", "deliver"}
    assert doc.node("deliver").executor is Executor.HUB  # type: ignore[union-attr]


def test_default_graph_prompts_are_inlined_not_paths() -> None:
    build = load_default_graph_doc().node("build")
    assert build is not None
    assert build.prompt is not None
    # Inlining replaced the ./prompts/build.md reference with the file's prose.
    assert not build.prompt.startswith("./")
    assert build.judgement is not None
    assert build.judgement.prompt is not None
    assert not build.judgement.prompt.startswith("./")


def test_default_graph_yaml_is_readable_text() -> None:
    assert "entry: build" in default_graph_yaml()
