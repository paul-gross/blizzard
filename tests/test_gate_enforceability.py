"""Which of a runner's gates can never be enforced — the classification rule (unit tier)."""

from __future__ import annotations

import pytest

from blizzard.foundation.gate_enforceability import UnenforceableGateReason
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.runners.gate_enforceability import UnenforceableGate, flag_unenforceable_gates

pytestmark = pytest.mark.unit

_EXECUTORS = {
    "build": frozenset({Executor.RUNNER}),
    "deliver": frozenset({Executor.HUB}),
    "mixed": frozenset({Executor.HUB, Executor.RUNNER}),
}


def test_a_gate_on_a_runner_node_is_enforceable() -> None:
    assert flag_unenforceable_gates(["build"], _EXECUTORS) == []


def test_a_gate_on_a_hub_only_node_is_flagged_hub_executed() -> None:
    assert flag_unenforceable_gates(["deliver"], _EXECUTORS) == [
        UnenforceableGate("deliver", UnenforceableGateReason.HUB_EXECUTED)
    ]


def test_a_gate_on_no_node_is_flagged_unknown_node() -> None:
    assert flag_unenforceable_gates(["no-such-node"], _EXECUTORS) == [
        UnenforceableGate("no-such-node", UnenforceableGateReason.UNKNOWN_NODE)
    ]


def test_a_node_with_mixed_executors_is_enforceable() -> None:
    assert flag_unenforceable_gates(["mixed"], _EXECUTORS) == []


def test_flags_keep_the_order_the_gates_were_given_in() -> None:
    flagged = flag_unenforceable_gates(["no-such-node", "build", "deliver"], _EXECUTORS)
    assert [g.node for g in flagged] == ["no-such-node", "deliver"]
