"""The packaged basic-harness-workflow graph (unit tier, issue #231).

``bas-hwf`` is the frontier-tier lane for harness work — the agentic harness's own
operating rules (skills, ``blizzard-context`` rules, graph prompts, agent docs) are
unusually high-leverage per token, so this lane pins the strongest capability tier
rather than the mechanical ``blizzard:basic`` tier ``bas-dwf`` uses. It is the same
lightweight shape as ``basic-development-workflow`` cut down one node further: no
``pre-push`` node at all — a rejected fast-forward routes straight back to ``build``,
the only station left that can rebase and revalidate.

This proves it loads, inlines its prompt file references, and passes mint-time
validation clean — so a fresh hub's ``POST /graphs`` of it can never be rejected.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.artifacts import ArtifactKind
from blizzard.hub.domain.graph import Executor, SessionMode
from blizzard.hub.domain.graph_validation import validate_graph
from blizzard.hub.graphs import _GRAPHS_DIR, load_graph_doc, packaged_graph_paths

pytestmark = pytest.mark.unit

_GRAPH_PATH = _GRAPHS_DIR / "basic-harness-workflow" / "graph.yaml"


def _doc():  # type: ignore[no-untyped-def]
    return load_graph_doc(_GRAPH_PATH)


def test_bas_hwf_validates_with_no_errors_or_warnings() -> None:
    result = validate_graph(_doc())
    assert result.ok, result.errors
    assert result.warnings == []


def test_bas_hwf_is_packaged() -> None:
    assert _GRAPH_PATH in packaged_graph_paths()


def test_bas_hwf_shape_is_the_lightweight_no_pre_push_lane() -> None:
    doc = _doc()
    assert doc.name == "bas-hwf"
    assert doc.entry == "build"
    assert [n.name for n in doc.nodes] == ["build", "review", "deliver", "retrospective"]
    assert doc.node("pre-push") is None  # the one structural cut from bas-dwf
    assert doc.node("plan") is None  # no plan-gate either, same as bas-dwf
    assert doc.node("build").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("review").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("deliver").executor is Executor.HUB  # type: ignore[union-attr]
    assert doc.node("retrospective").executor is Executor.RUNNER  # type: ignore[union-attr]


def test_bas_hwf_every_session_pool_pins_the_frontier_tier() -> None:
    doc = _doc()
    assert set(doc.sessions) == {"code", "gate"}
    assert doc.sessions["code"].model == ["blizzard:frontier"]
    assert doc.sessions["gate"].model == ["blizzard:frontier"]
    # The build lineage is bounded (it can accumulate across a review-fail or
    # deliver-conflict loop); the gate is only ever reached fresh, so it is not.
    assert doc.sessions["code"].rotate is not None
    assert doc.sessions["gate"].rotate is None


def test_bas_hwf_node_continuity() -> None:
    doc = _doc()
    assert (doc.node("build").session, doc.node("build").session_source) == (SessionMode.RESUME, "code")  # type: ignore[union-attr]
    assert (doc.node("review").session, doc.node("review").session_source) == (SessionMode.FRESH, "gate")  # type: ignore[union-attr]
    assert (doc.node("retrospective").session, doc.node("retrospective").session_source) == (SessionMode.RESUME, None)  # type: ignore[union-attr]


def test_bas_hwf_build_review_loop() -> None:
    doc = _doc()
    build = doc.node("build")
    assert build is not None and build.judgement is not None
    build_routes = {c.name: c.to for c in build.judgement.choices}
    assert build_routes == {"pass": "review", "fail": "build"}

    review = doc.node("review")
    assert review is not None and review.judgement is not None
    review_routes = {c.name: c.to for c in review.judgement.choices}
    assert review_routes == {"pass": "deliver", "fail": "build"}  # straight to deliver, no pre-push


def test_bas_hwf_deliver_routes_landed_to_retrospective_and_everything_else_to_build() -> None:
    doc = _doc()
    deliver = doc.node("deliver")
    assert deliver is not None and deliver.judgement is not None
    routes = {c.name: c.to for c in deliver.judgement.choices}
    # No pre-push node in this lane, so both non-landed outcomes bounce to build —
    # the only station left that can rebase and revalidate.
    assert routes == {"landed": "retrospective", "conflict": "build", "failure": "build"}


def test_bas_hwf_retrospective_closes_at_done() -> None:
    doc = _doc()
    retrospective = doc.node("retrospective")
    assert retrospective is not None and retrospective.judgement is not None
    routes = {c.name: c.to for c in retrospective.judgement.choices}
    assert routes == {"recorded": "done"}


def test_bas_hwf_produces() -> None:
    doc = _doc()
    build_produces = {(p.name, p.kind) for p in doc.node("build").produces}  # type: ignore[union-attr]
    assert ("commit", ArtifactKind.GIT_COMMIT) in build_produces
    assert any(p.name == "review-findings" for p in doc.node("review").produces)  # type: ignore[union-attr]
    assert any(p.name == "retrospective" for p in doc.node("retrospective").produces)  # type: ignore[union-attr]


def test_bas_hwf_prompts_are_inlined_not_paths() -> None:
    doc = _doc()
    for node in doc.nodes:
        if node.prompt is not None:
            assert not node.prompt.startswith("./")
        if node.judgement is not None and node.judgement.prompt is not None:
            assert not node.judgement.prompt.startswith("./")
