"""A chunk's progress through its graph — one reading per node, start to finish, from the chunk's visits alone."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.graph_progress_state import GraphProgressState
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.graph.model import Graph


@domain_model
@dataclass(frozen=True)
class NodeVisit:
    """One completed visit: the node left and the node it went to next, by name, at ``epoch``. A name is ``None``
    where it resolves to no node — the graph's end, or a node of a graph since deleted."""

    epoch: int
    from_name: str | None
    to_name: str | None


@domain_model
@dataclass(frozen=True)
class NodeProgress:
    node_id: str
    name: str
    state: GraphProgressState


def order_nodes(graph: Graph) -> list[str]:
    """The graph's node ids, start to finish: each node ranked by its longest forward path from the entry, ties kept
    in discovery order. A forward edge is any edge a depth-first walk from the entry does not close back onto a node
    still on its path, so a rework loop never pushes its target later. Nodes the entry never reaches follow, in the
    graph's own order."""
    id_by_name = {node.name: node.node_id for node in graph.nodes}
    out: dict[str, list[str]] = {node.node_id: [] for node in graph.nodes}
    for edge in graph.edges:
        to = id_by_name.get(edge.to_node_name) if edge.target_graph is None else None
        if to is not None and edge.from_node_id in out:
            out[edge.from_node_id].append(to)

    discovery: list[str] = []
    on_path: set[str] = set()
    seen: set[str] = set()
    forward: dict[str, list[str]] = {}

    def walk(node_id: str) -> None:
        seen.add(node_id)
        on_path.add(node_id)
        discovery.append(node_id)
        nexts: list[str] = []
        for to in out[node_id]:
            if to in on_path:
                continue
            nexts.append(to)
            if to not in seen:
                walk(to)
        forward[node_id] = nexts
        on_path.discard(node_id)

    if graph.entry_node_id in out:
        walk(graph.entry_node_id)

    # Longest forward path, relaxed until stable: the forward edges form a DAG, so this terminates.
    depth = dict.fromkeys(discovery, 0)
    changed = True
    while changed:
        changed = False
        for node_id in discovery:
            for to in forward[node_id]:
                if depth[node_id] + 1 > depth[to]:
                    depth[to] = depth[node_id] + 1
                    changed = True

    index = {node_id: i for i, node_id in enumerate(discovery)}
    reached = sorted(discovery, key=lambda node_id: (depth[node_id], index[node_id]))
    return reached + [node.node_id for node in graph.nodes if node.node_id not in seen]


def node_progress(graph: Graph, visits: Sequence[NodeVisit], current_node_id: str | None) -> list[NodeProgress]:
    """One reading per graph node, in :func:`order_nodes` order. Visits match nodes by name, not id: node ids are
    minted per graph version, so a chunk that followed its graph onto a newer version carries visits under the old
    version's ids. Visits are taken in epoch order, and one from a node this graph does not name is skipped. A visit
    failed when it routed back to its own node or one earlier in the order — a rework or a bounce — and passed
    otherwise; only a node's last visit counts. The node the chunk stands on is current whatever its visits say."""
    order = order_nodes(graph)
    name_by_id = {node.node_id: node.name for node in graph.nodes}
    rank = {name_by_id[node_id]: i for i, node_id in enumerate(order)}
    failed_by_name: dict[str, bool] = {}
    for visit in sorted(visits, key=lambda v: v.epoch):
        if visit.from_name is None or visit.from_name not in rank:
            continue
        to = rank.get(visit.to_name) if visit.to_name is not None else None
        failed_by_name[visit.from_name] = to is not None and to <= rank[visit.from_name]

    def state(node_id: str) -> GraphProgressState:
        if node_id == current_node_id:
            return GraphProgressState.CURRENT
        failed = failed_by_name.get(name_by_id[node_id])
        if failed is None:
            return GraphProgressState.UNVISITED
        return GraphProgressState.FAILED if failed else GraphProgressState.PASSED

    return [NodeProgress(node_id=node_id, name=name_by_id[node_id], state=state(node_id)) for node_id in order]
