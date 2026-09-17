"""Capability-matched acquisition (``bzh:domain-core``, ``bzh:domain-takes-objects``).
A statically-reachable **runner-owned session lineage** is every :class:`EffectiveSession`
a runner node along the graph from a given node could resolve to. A snapshot is eligible
for a chunk iff every such lineage is satisfied by one of its reported bindings; one
unsatisfied lineage anywhere makes the whole snapshot ineligible — this module answers
only that yes/no, never what to do with a ``False``."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.envelope import EffectiveSession
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Graph, Node
from blizzard.hub.domain.registry import RunnerCapability
from blizzard.hub.domain.work import Chunk


@dataclass(frozen=True)
class EligibilityCheck:
    """Whether ``capabilities`` can execute every runner-owned session lineage statically
    reachable from ``node`` — the pure predicate a later claim/peek gate reads. ``node`` is
    already resolved by the caller (the chunk's current node, or the graph's entry node if
    the chunk has not moved); this walks forward from whatever it is handed."""

    chunk: Chunk
    graph: Graph
    node: Node
    capabilities: Sequence[RunnerCapability]

    @property
    def eligible(self) -> bool:
        return all(self._lineage_satisfied(runner_node) for runner_node in self._reachable_runner_nodes())

    def _reachable_runner_nodes(self) -> list[Node]:
        """Every runner-owned node reached from :attr:`node`, DFS over the graph's edges,
        visiting each node id at most once (cycle-safe). A hub node is traversed through but
        contributes nothing itself; a cross-graph or terminal edge ends its path there,
        evaluating nothing past it."""
        seen: set[str] = set()
        runner_nodes: list[Node] = []
        stack = [self.node]
        while stack:
            current = stack.pop()
            if current.node_id in seen:
                continue
            seen.add(current.node_id)
            if current.executor is Executor.RUNNER:
                runner_nodes.append(current)
            for edge in self.graph.edges_from(current.node_id):
                if edge.target_graph is not None or edge.to_node_name == RESERVED_TERMINAL:
                    continue
                target = self.graph.node_by_name(edge.to_node_name)
                if target is not None:
                    stack.append(target)
        return runner_nodes

    def _lineage_satisfied(self, node: Node) -> bool:
        """Whether some reported capability could serve ``node``'s effective session,
        mirroring :class:`~blizzard.runner.loop.session.HarnessSelector`'s gate against a
        static snapshot rather than a live adapter."""
        session = EffectiveSession.of(self.chunk, self.graph, node)
        if not session.harnesses:
            return any(capability.default for capability in self.capabilities)
        strict = len(session.harnesses) > 1 and bool(session.model)
        for harness_id in session.harnesses:
            capability = next((c for c in self.capabilities if c.harness_id == harness_id), None)
            if capability is None:
                continue
            if strict and not any(tier in capability.tiers for tier in session.model):
                continue
            return True
        return False
