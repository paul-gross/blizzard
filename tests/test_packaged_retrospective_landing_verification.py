"""Both graphs' ``retrospective`` node states the landing-verification duty (issue #238).

Pinned mechanically against the loaded, inlined packaged prompt text. Scoped per lane:
advanced-development-workflow names a PR-merge leg; basic-development-workflow does not,
since it lands via a fast-forward.
"""

from __future__ import annotations

import pytest

from blizzard.hub.domain.graph import Executor, GraphDoc, NodeDoc
from blizzard.hub.graphs import PACKAGED

pytestmark = pytest.mark.unit


def _load(graph_name: str) -> GraphDoc:
    return PACKAGED.named(graph_name).doc


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
    # No PR to merge in this lane. Assert the prompt says so explicitly, not merely
    # that one incidental substring is absent.
    assert "no PR-merge leg to check" in prompt, "must say why there is no PR-merged leg"
    assert "no `resolve` node" in prompt, "must say a discrepancy is reported, not routed"
