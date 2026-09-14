"""Resolving the graphs a chunk request names and the names a chunk read renders."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import HTTPException, status

from blizzard.hub.domain.graph import Graph, GraphSummary, IReadGraphRepository


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
    """The graph summaries and node names one read resolves, primed in bulk and
    memoised by id (issue #421) — backed by :class:`IReadGraphRepository`'s narrow
    projections, never a full :class:`Graph`. An id nothing primed still resolves
    lazily, one graph at a time, the first time it's asked for."""

    graphs: IReadGraphRepository
    _summaries: dict[str, GraphSummary | None] = field(default_factory=dict)
    _node_names: dict[str, dict[str, str]] = field(default_factory=dict)

    def prime(self, graph_ids: Iterable[str | None]) -> None:
        """Resolve every id in ``graph_ids`` not already resolved, through one
        ``load_graph_summaries`` call and one ``load_node_names`` call, so a fleet's or a
        chunk's distinct graphs are read once each regardless of caller count. ``None``
        entries (an unset pin) are dropped, not looked up."""
        unresolved = sorted({graph_id for graph_id in graph_ids if graph_id is not None} - self._summaries.keys())
        if not unresolved:
            return
        summaries = self.graphs.load_graph_summaries(unresolved)
        node_names = self.graphs.load_node_names(unresolved)
        for graph_id in unresolved:
            self._summaries[graph_id] = summaries.get(graph_id)
            self._node_names[graph_id] = node_names.get(graph_id, {})

    def _summary(self, graph_id: str | None) -> GraphSummary | None:
        if graph_id is None:
            return None
        if graph_id not in self._summaries:
            self.prime([graph_id])
        return self._summaries[graph_id]

    def graph_name(self, graph_id: str | None) -> str | None:
        summary = self._summary(graph_id)
        return summary.name if summary is not None else None

    def entry_node_id(self, graph_id: str | None) -> str | None:
        summary = self._summary(graph_id)
        return summary.entry_node_id if summary is not None else None

    def created_at(self, graph_id: str | None) -> datetime | None:
        summary = self._summary(graph_id)
        return summary.created_at if summary is not None else None

    def node_name(self, graph_id: str | None, node_id: str | None) -> str | None:
        """``node_id``'s human name *in the graph that named it* (issue #90) — ``None`` when
        either is unresolvable, so a step from a graph since deleted degrades to its raw id."""
        if graph_id is None or node_id is None:
            return None
        if graph_id not in self._summaries:
            self.prime([graph_id])
        return self._node_names.get(graph_id, {}).get(node_id)
