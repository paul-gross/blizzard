"""Chunk restart — the operator's forced move of a chunk onto a node, now (issues #370, #371).

An **event**, not the standing intent a migration edit records: it lands a ``chunk.restarted``
fact at a fresh epoch, which fences the running attempt out and re-aims the chunk. Naming another
graph adds a migration fact for the re-pin. Everything the move consumes — the in-flight parks,
a standing intent — rides that one store write, so nothing survives it to re-park or re-aim."""

from __future__ import annotations

import threading

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.edit import MigrationTargetIsCurrentPin, TargetGraphRetired
from blizzard.hub.domain.graph import Graph, IReadGraphRepository, Node
from blizzard.hub.domain.work import TERMINAL_STATUSES, Chunk, ChunkFacts, ChunkStatus, IWriteChunkRepository

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
    """A restart resolved to a node the graph it lands on does not carry (#370, #371).

    Refused at request time, whether the name was typed or name-matched across graphs: the
    operator said where the chunk goes, and the landing graph's entry node is not it."""

    def __init__(self, node_name: str, graph_id: str) -> None:
        super().__init__(f"node {node_name!r} does not exist on graph {graph_id}")
        self.node_name = node_name
        self.graph_id = graph_id


class RestartCurrentNodeUnknown(Exception):
    """The chunk stands on a node its own pinned graph does not carry (issue #370).

    Refused rather than rewound to the entry node, as the claim path refuses one: the position
    is real, and defaulting it away would discard every node already come through."""

    def __init__(self, node_id: str, graph_id: str) -> None:
        super().__init__(f"chunk stands on node {node_id} which graph {graph_id} does not carry")
        self.node_id = node_id
        self.graph_id = graph_id


class RestartService:
    """Force a chunk onto a node at a fresh epoch — ``blizzard hub chunk restart``."""

    def __init__(
        self,
        *,
        chunks: IWriteChunkRepository,
        graphs: IReadGraphRepository,
        clock: IClock,
        claim_lock: threading.Lock,
    ) -> None:
        self._chunks = chunks
        # Read for one thing only — whether a cross-graph target is retired (issue #101). The
        # graphs themselves arrive resolved (``bzh:domain-takes-objects``).
        self._graphs = graphs
        self._clock = clock
        # Shared with the claim and edit paths (issue #120): this move reads the chunk's facts
        # and then writes against them, the same read-then-write those two serialize on.
        self._claim_lock = claim_lock

    def restart(
        self, chunk: Chunk, graph: Graph, *, node_name: str | None, by: str, to_graph: Graph | None = None
    ) -> int:
        """Move ``chunk`` onto ``node_name`` — its current node when unnamed — at a fresh epoch.

        ``to_graph`` makes it the eager cross-graph move (#371): a migration fact for the re-pin and a
        restart fact for the clean re-entry, in one write. Takes the resolved graphs
        (``bzh:domain-takes-objects``); every refusal writes nothing. Returns the ``chunk_restarts.id``."""
        with self._claim_lock:
            return self._restart_locked(chunk, graph, node_name=node_name, by=by, to_graph=to_graph)

    def _restart_locked(
        self, chunk: Chunk, graph: Graph, *, node_name: str | None, by: str, to_graph: Graph | None
    ) -> int:
        facts = self._chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
        status = facts.status()
        if status in TERMINAL_STATUSES:
            raise ChunkNotRestartable(chunk.chunk_id, status)
        if to_graph is not None:
            self._require_crossable(chunk, to_graph)
        from_node_id = facts.current_node_id()
        target = self._target(graph, to_graph, from_node_id, node_name)
        decision = facts.open_decision()
        # `record_restart` derives the fence epoch inside its own transaction — one above every
        # prior attempt, so the displaced worker's completion is rejected (`bzh:epoch-fencing`).
        return self._chunks.record_restart(
            chunk.chunk_id,
            from_node_id=from_node_id,
            to_node_id=target.node_id,
            by=by,
            at=self._clock.now(),
            decision_id=decision.decision_id if decision is not None else None,
            answered_question_ids=[q.question_id for q in facts.open_questions()],
            answer=SUPERSEDED_ANSWER,
            to_graph_id=to_graph.graph_id if to_graph is not None else None,
        )

    def _require_crossable(self, chunk: Chunk, to_graph: Graph) -> None:
        """The cross-graph target's own refusals (#371) — the pair an intended migration's target
        is held to, since this move records the same re-pin that intent's consult would."""
        if self._graphs.is_retired(to_graph.graph_id):
            raise TargetGraphRetired(to_graph.graph_id)
        if to_graph.graph_id == chunk.graph_id:
            raise MigrationTargetIsCurrentPin(to_graph.graph_id)

    @staticmethod
    def _target(graph: Graph, to_graph: Graph | None, from_node_id: str | None, node_name: str | None) -> Node:
        """The node the move lands on, resolved by name against the graph it lands on (#370, #371).

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
