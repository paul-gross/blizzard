"""Both graphs' ``retrospective`` node states the landing-verification duty (issue #238).

Pinned mechanically against the loaded, inlined packaged prompt text. Scoped per lane:
advanced-development-workflow names a PR-merge leg; basic-development-workflow does not,
since it lands via a fast-forward.

Each leg is pinned by the phrase that states the DUTY, not by a `gh` incantation — a
prompt spells out a command only where the choice it encodes is the instruction, which is
true of ``merge-base --is-ancestor`` (the alternatives answer a different question) and
not of reading a PR's state. Pinning the duty is also the stricter guard: ``gh run list
--commit`` appearing anywhere never proved it was the *gate* being queried, whereas the
phrases below pin both the check and the ref it keys on.
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
    assert "That repo's PR is merged" in prompt, "PR-merged leg missing"
    assert "work-items" in prompt, "work-item-closed leg missing"
    assert "the base branch's own gate red" in prompt, "merge-commit gate check missing"
    assert "by the PR's merge commit" in prompt, "gate check must key on the merge commit, not the branch"
    assert "delivery-incomplete" in prompt, "no pointer to the routing choice on a discrepancy"


def test_basic_retrospective_states_the_sha_and_work_item_legs_and_the_gate_check() -> None:
    prompt = _retrospective_node(_load("basic-development-workflow")).prompt or ""
    assert "Landing Verification" in prompt
    assert "merge-base --is-ancestor" in prompt, "sha-reachable-from-base leg missing"
    assert "work-items" in prompt, "work-item-closed leg missing"
    assert "the base branch's own gate red" in prompt, "merge-commit gate check missing"
    assert "by the fast-forwarded commit itself" in prompt, (
        "gate check must key on the fast-forwarded commit, not the branch"
    )
    # No PR to merge in this lane. Assert the prompt says so explicitly, not merely
    # that one incidental substring is absent.
    assert "no PR-merge leg to check" in prompt, "must say why there is no PR-merged leg"
    assert "no `resolve` node" in prompt, "must say a discrepancy is reported, not routed"
