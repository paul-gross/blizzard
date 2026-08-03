"""Both graphs' ``retrospective`` node states the landing-verification duty (unit tier,
issue #238 AC1/AC3).

Retrospective is the last node standing at the last trust boundary before a chunk closes,
and it is meant to re-derive deliver's landing report rather than take it on faith. An
AC that only prose satisfies is an AC nothing catches regressing — a future prompt edit
could drop the duty back to a bare assertion with no test failing. This guard pins the
three checks mechanically, mirroring ``test_packaged_prompt_session_claims.py``'s shape:
read straight off the loaded, inlined packaged prompt text.

Scoped to what each lane can actually check: advanced-development-workflow lands via a
per-repo PR merge (``land_pr_ci``), so its prompt names a PR-merge leg; basic-development-
workflow lands via a fast-forwarded base ref (``land_ff``), no PR involved, so its prompt
does not.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.graph import Executor, GraphDoc, NodeDoc
from blizzard.hub.graphs import _GRAPHS_DIR, load_graph_doc

pytestmark = pytest.mark.unit


def _load(graph_name: str) -> GraphDoc:
    return load_graph_doc(_GRAPHS_DIR / graph_name / "graph.yaml")


def _retrospective_node(doc: GraphDoc) -> NodeDoc:
    node = next(n for n in doc.nodes if n.name == "retrospective")
    assert node.executor is Executor.RUNNER
    return node


def test_advanced_retrospective_states_all_three_verification_legs_and_the_gate_check() -> None:
    prompt = _retrospective_node(_load("advanced-development-workflow")).prompt or ""
    assert "Landing Verification" in prompt
    assert "merge-base --is-ancestor" in prompt, "sha-reachable-from-base leg missing"
    assert "gh pr view" in prompt, "PR-merged leg missing"
    assert "work-items" in prompt, "work-item-closed leg missing"
    assert "gh run list --commit" in prompt, "merge-commit gate check missing"
    assert "delivery-incomplete" in prompt, "no pointer to the routing choice on a discrepancy"


def test_basic_retrospective_states_the_sha_and_work_item_legs_and_the_gate_check() -> None:
    prompt = _retrospective_node(_load("basic-development-workflow")).prompt or ""
    assert "Landing Verification" in prompt
    assert "merge-base --is-ancestor" in prompt, "sha-reachable-from-base leg missing"
    assert "work-items" in prompt, "work-item-closed leg missing"
    assert "gh run list --commit" in prompt, "merge-commit gate check missing"
    # No PR to merge in this lane — a fast-forward, not a PR merge. Assert the prompt
    # says so, not merely that one incidental substring is absent — a blanket "not in"
    # check would fail on any unrelated future mention of the same verb.
    assert "no PR-merge leg to check" in prompt, "must say why there is no PR-merged leg"
    assert "no `resolve` node" in prompt, "must say a discrepancy is reported, not routed"
