"""Where a chunk stood at an instant — a pure fold of its movement facts.

Transitions, migrations and restarts are folded; the chunk's current pin is a mutable value and is
read only for a chunk that never moved (``blizzard-product:/plans/tracing/fleet-spans/spec/spans.md``
§Where a step stood). Visits and positions compare node *names*, resolved against the graph each
movement fact names, because node ids are minted per graph version."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from blizzard.hub.domain.graph import RESERVED_TERMINAL, Graph
from blizzard.hub.domain.tracing.facts import StepFacts
from blizzard.hub.domain.work import MigrationFact

# Movement kinds rank as ``ChunkFacts.latest_movement`` ranks them on an exact tie.
_INITIAL_RANK = -1
_TRANSITION_RANK = 0
_MIGRATION_RANK = 1
_RESTART_RANK = 2

_BEGINNING = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class Arrival:
    """One arrival of the chunk at a node; the implicit entry placement has rank ``-1``."""

    recorded_at: datetime
    epoch: int
    rank: int
    graph_id: str
    node_id: str
    node_name: str

    def order(self) -> tuple[datetime, int, int]:
        return (self.recorded_at, self.epoch, self.rank)


@dataclass(frozen=True)
class Position:
    """A chunk's graph and node at an instant, and which arrival at that node's name it is."""

    graph_id: str
    node_id: str
    node_name: str
    visit: int


def _graph(facts: StepFacts, graph_id: str) -> Graph:
    try:
        return facts.graphs[graph_id]
    except KeyError:
        raise LookupError(f"graph {graph_id!r} is not in the step facts") from None


def _node_name(facts: StepFacts, graph_id: str, node_id: str) -> str:
    if node_id == RESERVED_TERMINAL:
        return RESERVED_TERMINAL
    node = _graph(facts, graph_id).node_by_id(node_id)
    if node is None:
        raise LookupError(f"node {node_id!r} is not in graph {graph_id!r}")
    return node.name


def _landed(facts: StepFacts, migration_from: str | None, from_graph_id: str, to_graph_id: str) -> str:
    from_name = _node_name(facts, from_graph_id, migration_from) if migration_from is not None else None
    return MigrationFact.landing_node(_graph(facts, to_graph_id), from_name)


def movement_arrivals(facts: StepFacts) -> tuple[Arrival, ...]:
    """Every movement fact as an arrival, in order — without the implicit entry placement."""
    arrivals: list[Arrival] = []
    for transition in facts.transitions:
        name = _node_name(facts, transition.graph_id, transition.to_node_id)
        arrivals.append(
            Arrival(
                transition.recorded_at,
                transition.epoch,
                _TRANSITION_RANK,
                transition.graph_id,
                transition.to_node_id,
                name,
            )
        )
    for migration in facts.migrations:
        landed = migration.landed_node_id or _landed(
            facts, migration.from_node_id, migration.from_graph_id, migration.to_graph_id
        )
        name = _node_name(facts, migration.to_graph_id, landed)
        arrivals.append(
            Arrival(migration.recorded_at, migration.epoch, _MIGRATION_RANK, migration.to_graph_id, landed, name)
        )
    for restart in facts.restarts:
        name = _node_name(facts, restart.graph_id, restart.to_node_id)
        arrivals.append(
            Arrival(restart.recorded_at, restart.epoch, _RESTART_RANK, restart.graph_id, restart.to_node_id, name)
        )
    return tuple(sorted(arrivals, key=Arrival.order))


def _starting_graph_id(facts: StepFacts, arrivals: tuple[Arrival, ...]) -> str:
    """The graph the chunk stood in before its first movement, read from facts, not the pin."""
    if not arrivals:
        if facts.pin_graph_id is None:
            raise LookupError("a chunk with no movement fact needs pin_graph_id")
        return facts.pin_graph_id
    first = arrivals[0]
    for transition in facts.transitions:
        if (transition.recorded_at, transition.epoch, _TRANSITION_RANK) == first.order():
            return transition.graph_id
    for migration in facts.migrations:
        if (migration.recorded_at, migration.epoch, _MIGRATION_RANK) == first.order():
            return migration.from_graph_id
    for restart in facts.restarts:
        if (restart.recorded_at, restart.epoch, _RESTART_RANK) == first.order():
            return restart.from_graph_id or restart.graph_id
    raise LookupError("the earliest movement fact was not found")  # pragma: no cover


def arrivals_with_entry(facts: StepFacts) -> tuple[Arrival, ...]:
    """The movement arrivals led by the entry placement, which is the chunk's first visit."""
    arrivals = movement_arrivals(facts)
    graph = _graph(facts, _starting_graph_id(facts, arrivals))
    entry = Arrival(
        _BEGINNING,
        0,
        _INITIAL_RANK,
        graph.graph_id,
        graph.entry_node_id,
        _node_name(facts, graph.graph_id, graph.entry_node_id),
    )
    return (entry, *arrivals)


def position_at(facts: StepFacts, instant: datetime) -> Position:
    """The chunk's graph, node and visit at ``instant`` — arrivals recorded at or before it."""
    seen = [a for a in arrivals_with_entry(facts) if a.recorded_at <= instant]
    current = seen[-1]
    visit = sum(1 for a in seen if a.node_name == current.node_name)
    return Position(current.graph_id, current.node_id, current.node_name, visit)


def position_of_node(facts: StepFacts, node_id: str, instant: datetime) -> Position:
    """The position of a gate's own node — resolved against whichever supplied graph contains it."""
    for graph in facts.graphs.values():
        node = graph.node_by_id(node_id)
        if node is None:
            continue
        seen = [a for a in arrivals_with_entry(facts) if a.recorded_at <= instant and a.node_name == node.name]
        return Position(graph.graph_id, node_id, node.name, max(len(seen), 1))
    raise LookupError(f"node {node_id!r} is in none of the supplied graphs")
