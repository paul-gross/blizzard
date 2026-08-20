"""Resolving the graphs a chunk request names and the names a chunk read renders."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import HTTPException, status

from blizzard.hub.domain.graph import Graph, IReadGraphRepository


def graph_by_ref(graphs: IReadGraphRepository, ref: str) -> Graph:
    """The graph a request names, by id or by name — a name resolving to its newest enabled mint.

    404 when neither does: a name whose every mint is retired reads as unknown here (issue #101),
    while a retired graph named by id resolves and is refused by the domain instead."""
    graph = graphs.get(ref) or graphs.get_enabled_by_name(ref)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {ref}")
    return graph


@dataclass
class GraphNames:
    """The graphs one read resolves, memoised by id — including the misses.

    A whole-fleet read shares one instance so each graph is fetched once however many
    chunks or history steps name it; a single-chunk read makes its own."""

    lookup: Callable[[str], Graph | None]
    _resolved: dict[str | None, Graph | None] = field(default_factory=dict)

    def graph(self, graph_id: str | None) -> Graph | None:
        if graph_id not in self._resolved:
            self._resolved[graph_id] = self.lookup(graph_id) if graph_id is not None else None
        return self._resolved[graph_id]

    def graph_name(self, graph_id: str | None) -> str | None:
        graph = self.graph(graph_id)
        return graph.name if graph is not None else None

    def node_name(self, graph_id: str | None, node_id: str | None) -> str | None:
        """``node_id``'s human name *in the graph that named it* (issue #90) — ``None`` when
        either is unresolvable, so a step from a graph since deleted degrades to its raw id."""
        graph = self.graph(graph_id)
        if graph is None or node_id is None:
            return None
        node = graph.node_by_id(node_id)
        return node.name if node is not None else None
