"""Which of a runner's configured gates no graph will ever run on a runner.

A runner's gate set covers only the nodes the runner executes, and the hub never enforces it. Nothing here is stored:
the flag is derived on every read from the tenant's current graphs, so a graph that gains the node later clears it. A
flagged gate is never refused — it is only reported."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass

from blizzard.foundation.gate_enforceability import UnenforceableGateReason
from blizzard.foundation.node_steps import Executor
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class UnenforceableGate:
    """One gate flagged as not enforceable, and why."""

    node: str
    reason: UnenforceableGateReason


def flag_unenforceable_gates(
    gates: Collection[str], executors_by_node: Mapping[str, Collection[Executor]]
) -> list[UnenforceableGate]:
    """The gates in ``gates`` no non-retired graph will run on a runner, in the order given.

    ``executors_by_node`` maps a node name to the executors it has across every non-retired graph. A node that any
    graph runs on a runner is enforceable, however many others run it on the hub."""
    flagged: list[UnenforceableGate] = []
    for gate in gates:
        executors = executors_by_node.get(gate)
        if not executors:
            flagged.append(UnenforceableGate(gate, UnenforceableGateReason.UNKNOWN_NODE))
        elif Executor.RUNNER not in executors:
            flagged.append(UnenforceableGate(gate, UnenforceableGateReason.HUB_EXECUTED))
    return flagged
