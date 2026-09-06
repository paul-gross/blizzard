"""The packaged basic-harness-workflow graph (unit tier, issue #231).

Proves ``bas-hwf`` loads, inlines its prompt file references, and passes mint-time
validation clean — so a fresh hub's ``POST /graphs`` of it can never be rejected.
"""

from __future__ import annotations

import pytest

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.node_steps import Executor, SessionMode
from blizzard.hub.domain.graph_validation import Validator
from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit

_GRAPH = PACKAGED.named("basic-harness-workflow")


def _doc():  # type: ignore[no-untyped-def]
    return _GRAPH.doc


def test_bas_hwf_validates_with_no_errors_or_warnings() -> None:
    result = Validator.of(_doc()).result
    assert result.ok, result.errors
    assert result.warnings == []


def test_bas_hwf_is_packaged() -> None:
    assert _GRAPH.path in PACKAGED.paths


def test_bas_hwf_shape_is_the_frontier_build_advanced_gate_and_iterate_lane() -> None:
    doc = _doc()
    assert doc.name == "bas-hwf"
    assert doc.entry == "build"
    assert [n.name for n in doc.nodes] == ["build", "review", "iterate", "deliver", "retrospective"]
    assert doc.node("pre-push") is None  # phase 2 adds this
    assert doc.node("plan") is None  # no plan-gate either, same as bas-dwf
    assert doc.node("build").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("review").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("iterate").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("deliver").executor is Executor.HUB  # type: ignore[union-attr]
    assert doc.node("retrospective").executor is Executor.RUNNER  # type: ignore[union-attr]


def test_bas_hwf_session_pools_retier_the_iterate_loop() -> None:
    doc = _doc()
    assert set(doc.sessions) == {"code", "gate", "iteration"}
    assert doc.sessions["code"].model == ["blizzard:frontier"]
    assert doc.sessions["gate"].model == ["blizzard:frontier"]
    assert doc.sessions["iteration"].model == ["blizzard:advanced"]
    # The build and iterate lineages are bounded — each can accumulate across a loop;
    # the gate is only ever reached fresh, so it is not.
    assert doc.sessions["code"].rotate is not None
    assert doc.sessions["iteration"].rotate is not None
    assert doc.sessions["gate"].rotate is None


def test_bas_hwf_node_continuity() -> None:
    doc = _doc()
    assert (doc.node("build").session, doc.node("build").session_source) == (SessionMode.RESUME, "code")  # type: ignore[union-attr]
    assert (doc.node("review").session, doc.node("review").session_source) == (SessionMode.FRESH, "gate")  # type: ignore[union-attr]
    assert (doc.node("iterate").session, doc.node("iterate").session_source) == (SessionMode.RESUME, "iteration")  # type: ignore[union-attr]
    assert (doc.node("retrospective").session, doc.node("retrospective").session_source) == (SessionMode.RESUME, None)  # type: ignore[union-attr]


def test_bas_hwf_build_review_iterate_loop() -> None:
    doc = _doc()
    build = doc.node("build")
    assert build is not None and build.judgement is not None
    build_routes = {c.name: c.to for c in build.judgement.choices}
    assert build_routes == {"pass": "review", "fail": "build"}

    review = doc.node("review")
    assert review is not None and review.judgement is not None
    review_routes = {c.name: c.to for c in review.judgement.choices}
    assert review_routes == {"pass": "deliver", "fail": "iterate"}  # the advanced-tier fixer loop

    iterate = doc.node("iterate")
    assert iterate is not None and iterate.judgement is not None
    iterate_routes = {c.name: c.to for c in iterate.judgement.choices}
    assert iterate_routes == {"pass": "review", "fail": "iterate"}


def test_bas_hwf_deliver_routes_landed_to_retrospective_and_everything_else_to_build() -> None:
    doc = _doc()
    deliver = doc.node("deliver")
    assert deliver is not None and deliver.judgement is not None
    routes = {c.name: c.to for c in deliver.judgement.choices}
    # Phase 2 retargets these to pre-push; until then the only station that can
    # rebase and revalidate is build.
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


def test_bas_hwf_review_finding_refutes_is_produced_only_by_iterate() -> None:
    doc = _doc()
    producing = {node.name for node in doc.nodes if any(p.name == "review-finding-refutes" for p in node.produces)}
    assert producing == {"iterate"}
    iterate_produces = {(p.name, p.kind) for p in doc.node("iterate").produces}  # type: ignore[union-attr]
    assert ("commit", ArtifactKind.GIT_COMMIT) in iterate_produces


def test_bas_hwf_prompts_are_inlined_not_paths() -> None:
    doc = _doc()
    for node in doc.nodes:
        if node.prompt is not None:
            assert not node.prompt.startswith("./")
        if node.judgement is not None and node.judgement.prompt is not None:
            assert not node.judgement.prompt.startswith("./")
