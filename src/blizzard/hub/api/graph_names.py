"""Resolving the graphs a chunk request names and the names a chunk read renders."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from functools import partial

from blizzard.hub.api import problems_admin
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.domain.graph.model import Graph, GraphSummary, IReadGraphRepository


def graph_by_ref(graphs: IReadGraphRepository, ref: str) -> Graph:
    """The graph a request names, by id or by name — a name resolving to its newest enabled mint.

    404 when neither does: a name whose every mint is retired reads as unknown here,
    while a retired graph named by id resolves and is refused by the domain instead."""
    graph = graphs.get(ref) or graphs.get_enabled_by_name(ref)
    if graph is None:
        raise ProblemHTTPException(partial(problems_admin.graph_not_found_problem, f"unknown graph {ref}"))
    return graph


@dataclass
class GraphNames:
    """The graph summaries and node names one read resolves, primed in bulk and
    memoised by id — backed by :class:`IReadGraphRepository`'s narrow
    projections; only :meth:`graph` reifies a full :class:`Graph`, primed apart by
    :meth:`prime_graphs`. An id nothing primed still resolves lazily, one graph at a
    time, the first time it's asked for."""

    graphs: IReadGraphRepository
    _summaries: dict[str, GraphSummary | None] = field(default_factory=lambda: {})
    _node_names: dict[str, dict[str, str]] = field(default_factory=lambda: {})
    _graphs: dict[str, Graph | None] = field(default_factory=lambda: {})

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

    def prime_graphs(self, graph_ids: Iterable[str | None]) -> None:
        """Reify every graph in ``graph_ids`` not already reified, through one ``get_many`` call."""
        unresolved = sorted({graph_id for graph_id in graph_ids if graph_id is not None} - self._graphs.keys())
        if not unresolved:
            return
        graphs = self.graphs.get_many(unresolved)
        for graph_id in unresolved:
            self._graphs[graph_id] = graphs.get(graph_id)

    def graph(self, graph_id: str | None) -> Graph | None:
        """``graph_id`` reified in full — nodes and edges — or ``None`` when it is unknown."""
        if graph_id is None:
            return None
        if graph_id not in self._graphs:
            self.prime_graphs([graph_id])
        return self._graphs[graph_id]

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
        """``node_id``'s human name *in the graph that named it* — ``None`` when
        either is unresolvable, so a step from a graph since deleted degrades to its raw id."""
        if graph_id is None or node_id is None:
            return None
        if graph_id not in self._summaries:
            self.prime([graph_id])
        return self._node_names.get(graph_id, {}).get(node_id)
