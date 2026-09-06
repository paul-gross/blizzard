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


def test_bas_hwf_shape_is_the_six_node_frontier_build_advanced_gate_lane() -> None:
    doc = _doc()
    assert doc.name == "bas-hwf"
    assert doc.entry == "build"
    assert [n.name for n in doc.nodes] == ["build", "review", "iterate", "pre-push", "deliver", "retrospective"]
    assert doc.node("plan") is None  # no plan-gate, same as bas-dwf
    assert doc.node("build").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("review").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("iterate").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("pre-push").executor is Executor.RUNNER  # type: ignore[union-attr]
    assert doc.node("deliver").executor is Executor.HUB  # type: ignore[union-attr]
    assert doc.node("retrospective").executor is Executor.RUNNER  # type: ignore[union-attr]


def test_bas_hwf_four_pools_retier_review_iterate_and_prepush_off_frontier() -> None:
    doc = _doc()
    assert set(doc.sessions) == {"code", "gate", "iteration", "prepush"}
    # Frontier authors once; every gate and loop behind it runs advanced.
    assert doc.sessions["code"].model == ["blizzard:frontier"]
    assert doc.sessions["gate"].model == ["blizzard:advanced"]
    assert doc.sessions["iteration"].model == ["blizzard:advanced"]
    assert doc.sessions["prepush"].model == ["blizzard:advanced"]
    # The build, iterate, and pre-push lineages are each bounded — every one can
    # accumulate across its own loop; the gate is only ever reached fresh, so it is not.
    assert doc.sessions["code"].rotate is not None
    assert doc.sessions["iteration"].rotate is not None
    assert doc.sessions["prepush"].rotate is not None
    assert doc.sessions["gate"].rotate is None
    # A uniform ceiling above every bounded lineage's rotate bound, inert on gate.
    for pool in ("code", "gate", "iteration", "prepush"):
        assert doc.sessions[pool].compaction_window == "450000"


def test_bas_hwf_node_continuity() -> None:
    doc = _doc()
    assert (doc.node("build").session, doc.node("build").session_source) == (SessionMode.RESUME, "code")  # type: ignore[union-attr]
    assert (doc.node("review").session, doc.node("review").session_source) == (SessionMode.FRESH, "gate")  # type: ignore[union-attr]
    assert (doc.node("iterate").session, doc.node("iterate").session_source) == (SessionMode.RESUME, "iteration")  # type: ignore[union-attr]
    assert (doc.node("pre-push").session, doc.node("pre-push").session_source) == (SessionMode.RESUME, "prepush")  # type: ignore[union-attr]
    retrospective = doc.node("retrospective")
    assert retrospective is not None
    # Explicit, not bare resume: unlike bas-dwf, the prepush lineage never saw build
    # or iterate, so retrospective must pin the same pool pre-push actually ran on.
    assert (retrospective.session, retrospective.session_source) == (SessionMode.RESUME, "prepush")


def test_bas_hwf_target_routing_table() -> None:
    """The full routing table from blizzard#493."""
    doc = _doc()

    def routes(name: str) -> dict[str, str | None]:
        node = doc.node(name)
        assert node is not None and node.judgement is not None
        return {c.name: c.to for c in node.judgement.choices}

    assert routes("build") == {"pass": "review", "fail": "build"}
    assert routes("review") == {"pass": "pre-push", "fail": "iterate"}
    assert routes("iterate") == {"pass": "review", "fail": "iterate"}
    assert routes("pre-push") == {"clean": "deliver", "insignificant": "review", "significant": "iterate"}
    assert routes("deliver") == {"landed": "retrospective", "conflict": "pre-push", "failure": "pre-push"}
    assert routes("retrospective") == {"recorded": "done"}


def test_bas_hwf_produces() -> None:
    doc = _doc()
    build_produces = {(p.name, p.kind) for p in doc.node("build").produces}  # type: ignore[union-attr]
    assert ("commit", ArtifactKind.GIT_COMMIT) in build_produces
    assert any(p.name == "review-findings" for p in doc.node("review").produces)  # type: ignore[union-attr]
    assert any(p.name == "retrospective" for p in doc.node("retrospective").produces)  # type: ignore[union-attr]


def test_bas_hwf_pre_push_redeclares_the_commit_and_produces_retrospective() -> None:
    doc = _doc()
    pre_push_produces = {(p.name, p.kind) for p in doc.node("pre-push").produces}  # type: ignore[union-attr]
    assert ("commit", ArtifactKind.GIT_COMMIT) in pre_push_produces
    assert any(p.name == "pre-push-summary" for p in doc.node("pre-push").produces)  # type: ignore[union-attr]
    assert any(p.name == "retrospective" for p in doc.node("pre-push").produces)  # type: ignore[union-attr]


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
