"""Chunk restart — the operator's forced move of a chunk onto a node, now.

An **event**, not the standing intent a migration edit records: it lands a ``chunk.restarted``
fact at a fresh epoch, which fences the running attempt out and re-aims the chunk. Naming another
graph adds a migration fact for the re-pin. Everything the move consumes — the in-flight parks,
a standing intent — rides that one store write, so nothing survives it to re-park or re-aim."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.graph.model import Graph, GraphStanding, IReadGraphRepository, Node
from blizzard.hub.domain.operations.edit import MigrationTargetIsCurrentPin

#: The answer an open ask is consumed with. Fixed and toneless: the person who moved the
#: chunk did not answer the question, they made it moot.
SUPERSEDED_ANSWER = "The node-step that asked this was superseded by an operator restart; no answer applies."


class ChunkNotRestartable(Exception):
    """A restart targeted a terminal chunk ({done, stopped}) — there is nothing to re-enter."""

    def __init__(self, chunk_id: str, status: ChunkStatus) -> None:
        super().__init__(f"chunk {chunk_id} is {status.value}, not restartable")
        self.chunk_id = chunk_id
        self.status = status


class RestartNodeUnknown(Exception):
    """A restart resolved to a node the graph it lands on does not carry.

    Refused at request time, whether the name was typed or name-matched across graphs: the
    operator said where the chunk goes, and the landing graph's entry node is not it."""

    def __init__(self, node_name: str, graph_id: str) -> None:
        super().__init__(f"node {node_name!r} does not exist on graph {graph_id}")
        self.node_name = node_name
        self.graph_id = graph_id


class RestartGraphPinChanged(Exception):
    """A same-graph restart's ``graph`` argument no longer matches the chunk's current
    pin, re-derived under the row lock — a concurrent edit re-pinned it between the
    caller's load and this lock. Refused rather than resolved against the stale graph
    object the caller passed in: the caller must re-load and retry, naming ``to_graph``
    explicitly if the move should follow the graph the chunk now stands on."""

    def __init__(self, chunk_id: str, expected_graph_id: str, actual_graph_id: str) -> None:
        super().__init__(
            f"chunk {chunk_id}'s graph pin changed from {expected_graph_id} to {actual_graph_id} "
            "since it was loaded; restart refused rather than resolved against the stale graph"
        )
        self.chunk_id = chunk_id
        self.expected_graph_id = expected_graph_id
        self.actual_graph_id = actual_graph_id


class RestartCurrentNodeUnknown(Exception):
    """The chunk stands on a node its own pinned graph does not carry.

    Refused rather than rewound to the entry node, as the claim path refuses one: the position
    is real, and defaulting it away would discard every node already come through."""

    def __init__(self, node_id: str, graph_id: str) -> None:
        super().__init__(f"chunk stands on node {node_id} which graph {graph_id} does not carry")
        self.node_id = node_id
        self.graph_id = graph_id


def require_restartable(chunk_id: str, facts: ChunkFacts) -> None:
    """Refuse a restart outside :attr:`ChunkVerb.RESTART`'s window with :class:`ChunkNotRestartable`."""
    if not facts.admits(ChunkVerb.RESTART):
        raise ChunkNotRestartable(chunk_id, facts.status())


def require_crossable(chunk: Chunk, to_graph: Graph, *, retired: bool) -> None:
    """The cross-graph target's own refusals — the pair an intended migration's target is held
    to, since this move records the same re-pin that intent's consult would."""
    GraphStanding(to_graph, retired=retired).require_targetable()
    if to_graph.graph_id == chunk.graph_id:
        raise MigrationTargetIsCurrentPin(to_graph.graph_id)


def restart_landing(graph: Graph, to_graph: Graph | None, from_node_id: str | None, node_name: str | None) -> Node:
    """The node the move lands on, resolved by name against the graph it lands on.

    Unnamed, it is the chunk's current node, name-matched onto ``to_graph`` when crossing — `auto`
    migration's landing rule minus the entry fallback. A chunk that has not moved stands on nowhere,
    so the landing graph's entry is the one derived default; one that HAS moved is refused there."""
    landing = to_graph if to_graph is not None else graph
    if node_name is not None:
        named = landing.node_by_name(node_name)
        if named is None:
            raise RestartNodeUnknown(node_name, landing.graph_id)
        return named
    if from_node_id is None:
        entry = landing.node_by_id(landing.entry_node_id)
        assert entry is not None, f"graph {landing.graph_id} names an entry node it does not carry"
        return entry
    current = graph.node_by_id(from_node_id)
    if current is None:
        raise RestartCurrentNodeUnknown(from_node_id, graph.graph_id)
    if to_graph is None:
        return current
    matched = to_graph.node_by_name(current.name)
    if matched is None:
        raise RestartNodeUnknown(current.name, to_graph.graph_id)
    return matched


@domain_model
@dataclass(frozen=True)
class RestartPlan:
    """:func:`plan_restart`'s decision — everything the one restart write records."""

    from_node_id: str | None
    to_node_id: str
    #: The open gate the move closes undecided, if any.
    decision_id: str | None
    #: The open asks the move consumes, each answered with :data:`SUPERSEDED_ANSWER`.
    answered_question_ids: list[str]
    answer: str
    #: The graph a cross-graph move re-pins onto; ``None`` for a same-graph move.
    to_graph_id: str | None


def plan_restart(
    chunk: Chunk,
    facts: ChunkFacts,
    graph: Graph,
    *,
    node_name: str | None,
    to_graph: Graph | None = None,
    to_graph_retired: bool = False,
) -> RestartPlan:
    """Decide a restart of ``chunk`` (its record as it stands under the row lock) at ``facts``.

    Refuses a terminal chunk, a cross-graph target that is retired or the current pin, a
    same-graph ``graph`` that is no longer the chunk's pin, and an unresolvable landing node;
    otherwise records what the move consumes — the open gate, the open asks, the re-pin."""
    require_restartable(chunk.chunk_id, facts)
    if to_graph is not None:
        require_crossable(chunk, to_graph, retired=to_graph_retired)
    elif chunk.graph_id != graph.graph_id:
        raise RestartGraphPinChanged(chunk.chunk_id, graph.graph_id, chunk.graph_id)
    from_node_id = facts.current_node_id()
    target = restart_landing(graph, to_graph, from_node_id, node_name)
    decision = facts.open_decision()
    return RestartPlan(
        from_node_id=from_node_id,
        to_node_id=target.node_id,
        decision_id=decision.decision_id if decision is not None else None,
        answered_question_ids=[q.question_id for q in facts.open_questions()],
        answer=SUPERSEDED_ANSWER,
        to_graph_id=to_graph.graph_id if to_graph is not None else None,
    )


class RestartService:
    """Force a chunk onto a node at a fresh epoch — ``blizzard hub chunk restart``."""

    def __init__(
        self,
        *,
        movement: IWriteChunkMovementRepository,
        graphs: IReadGraphRepository,
        clock: IClock,
        exclusive: IChunkExclusiveWrites,
    ) -> None:
        self._movement = movement
        # Read for one thing only — whether a cross-graph target is retired. The
        # graphs themselves arrive resolved (``bzh:domain-takes-objects``).
        self._graphs = graphs
        self._clock = clock
        # Read and write the chunk's facts under one row lock (``bzh:store-exclusive-write``).
        self._exclusive = exclusive

    def restart(
        self, chunk: Chunk, graph: Graph, *, node_name: str | None, by: str, to_graph: Graph | None = None
    ) -> int:
        """Move ``chunk`` onto ``node_name`` — its current node when unnamed — at a fresh epoch.

        ``to_graph`` makes it the eager cross-graph move: a migration fact and a restart fact in one
        write. :func:`plan_restart` decides against the facts and the record re-read under the row
        lock; every refusal writes nothing. Returns the ``chunk_restarts.id``."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            # A `None` load means gone under this lock — refuse rather than substitute a
            # synthetic status, mirroring `DeleteService.delete`/`DependencyService.declare`.
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            # Re-read fresh under the lock: a concurrent edit that re-pinned ``graph_id``
            # between the caller's own load and this lock must not have the plan answered
            # against a pin it already changed.
            current = handle.record(chunk.chunk_id)
            if current is None:
                raise ChunkNotFound(chunk.chunk_id)
            plan = plan_restart(
                current,
                facts,
                graph,
                node_name=node_name,
                to_graph=to_graph,
                to_graph_retired=to_graph is not None and self._graphs.is_retired(to_graph.graph_id),
            )
            # `record_restart_locked` derives the fence epoch inside its own transaction —
            # one above every prior attempt, so the displaced worker's completion is
            # rejected (`bzh:epoch-fencing`).
            return self._movement.record_restart_locked(
                handle,
                chunk.chunk_id,
                from_node_id=plan.from_node_id,
                to_node_id=plan.to_node_id,
                by=by,
                at=self._clock.now(),
                decision_id=plan.decision_id,
                answered_question_ids=plan.answered_question_ids,
                answer=plan.answer,
                to_graph_id=plan.to_graph_id,
            )
