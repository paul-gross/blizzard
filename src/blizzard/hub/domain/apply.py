"""Completion apply — the advancement checkpoint.

``POST /chunks/{id}/completions`` submits one node-step's completion; this rule
applies it. The write is **atomic** (the transition and its artifacts land together,
D-036), **epoch-fenced** (a submission whose epoch is not the chunk's latest is
rejected before anything is written — a zombie's work never lands, D-007), and
**idempotent** (a re-applied completion — the lost-response replay, D-090 — returns
the same outcome without a second transition).

The apply-response is what lets the runner continue in place: a runner node
returns the next envelope; a hub node (deliver) is taken over by the coordinator and
returns ``hub_node_taken``; the reserved terminal returns ``done``; a human gate
parks the chunk on an open **Decision** (``parked_at_gate``, D-045). Ordering matters
— the idempotency probe runs **before** the terminal check, so replaying the very
completion that delivered the chunk still returns its original outcome rather than a
spurious ``failure``.

Human gates cut two ways here. A transition **into** a human-judged node opens
a decision and parks (the graph gate). A transition **out of** one is only legal as
the **resolving transition** — a completion carrying the resolved decision's id;
a plain worker transition out of a gate is rejected (human signoff required).
"""

from __future__ import annotations

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ARTIFACT_PREFIX, DECISION_PREFIX, TRANSITION_PREFIX, mint
from blizzard.hub.delivery.coordinator import MergeQueueCoordinator
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.envelope import build_node_envelope
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Edge, Executor, Graph, JudgedBy, Node
from blizzard.hub.domain.work import (
    Chunk,
    ChunkStatus,
    DecisionChoice,
    IWriteChunkRepository,
    derive_chunk_status,
    latest_epoch,
)
from blizzard.wire.completion import CompletionSubmission, SubmittedArtifact
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse

_TERMINAL_STATUSES = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})


def _failure(detail: str) -> ApplyResponse:
    return ApplyResponse(outcome=ApplyOutcome.FAILURE, detail=detail)


class ApplyService:
    """Apply a node-step completion to a chunk, fenced and idempotent."""

    def __init__(
        self,
        *,
        chunks: IWriteChunkRepository,
        coordinator: MergeQueueCoordinator,
        clock: IClock,
    ) -> None:
        self._chunks = chunks
        self._coordinator = coordinator
        self._clock = clock

    def apply(self, chunk: Chunk, graph: Graph, submission: CompletionSubmission) -> ApplyResponse:
        from_node = graph.node_by_id(submission.from_node_id)
        if from_node is None:
            return _failure(f"no node {submission.from_node_id} in graph {graph.graph_id}")

        # Idempotent replay first: a completion already applied at this
        # (node, epoch) returns its original outcome — even once the chunk is terminal.
        # This covers both an ordinary transition and a gate-resolving one (same key).
        replayed = self._chunks.accepted_transition_target(
            chunk.chunk_id, from_node_id=submission.from_node_id, epoch=submission.epoch
        )
        if replayed is not None:
            return self._respond(chunk, graph, from_node, submission, to_node_id=replayed, run_coordinator=False)

        # A completion carrying a decision id is a gate-resolving transition —
        # graph gate (human node) or runner-config gate (worker node): validate and
        # record it against the resolved decision, marking that decision transitioned.
        if submission.decision_id is not None:
            return self._apply_gate_resolution(chunk, graph, from_node, submission)
        # A plain transition OUT of a human-judged node is rejected — human signoff
        # required; only the resolving transition above may leave a gate node.
        if from_node.judged_by is JudgedBy.HUMAN:
            return _failure(f"human signoff required: node `{from_node.name}` is a gate — resolve its decision")

        facts = self._chunks.load_facts(chunk.chunk_id)
        if facts is None:
            return _failure(f"unknown chunk {chunk.chunk_id}")
        if derive_chunk_status(facts) in _TERMINAL_STATUSES:
            return _failure("chunk is terminal")
        latest = latest_epoch(facts)
        if latest is not None and submission.epoch != latest:
            return _failure(f"stale epoch {submission.epoch}; chunk is at {latest}")

        edge = graph.edge_for_choice(from_node.node_id, submission.choice)
        if edge is None:
            return _failure(f"node {from_node.name} has no choice `{submission.choice}`")
        to_node_id = RESERVED_TERMINAL if edge.to_node_name == RESERVED_TERMINAL else _resolve(graph, edge.to_node_name)
        if to_node_id is None:
            return _failure(f"choice `{submission.choice}` routes to unknown node {edge.to_node_name}")

        self._chunks.record_transition(
            transition_id=mint(TRANSITION_PREFIX, self._clock),
            chunk_id=chunk.chunk_id,
            from_node_id=from_node.node_id,
            to_node_id=to_node_id,
            choice_name=submission.choice,
            epoch=submission.epoch,
            runner_id=submission.runner_id,
            at=self._clock.now(),
            artifacts=[self._row(chunk, from_node, submission.epoch, a) for a in submission.artifacts],
        )
        return self._respond(
            chunk, graph, from_node, submission, to_node_id=to_node_id, run_coordinator=True, edge=edge
        )

    def _apply_gate_resolution(
        self, chunk: Chunk, graph: Graph, gate_node: Node, submission: CompletionSubmission
    ) -> ApplyResponse:
        """Advance a chunk past a resolved gate — the resolving transition.

        The runner picks the resolution up on PULL and submits this to record the
        transition along the chosen edge, referencing the decision (which marks it
        transitioned). Works for both a graph gate (human node) and a runner-config gate
        (worker node); the decision's artifacts already landed, so this carries none."""
        assert submission.decision_id is not None  # the caller dispatches only when set
        decision = self._chunks.get_decision(submission.decision_id)
        if decision is None or decision.chunk_id != chunk.chunk_id or decision.node_id != gate_node.node_id:
            return _failure(f"decision {submission.decision_id} does not match node `{gate_node.name}`")
        if decision.resolved_choice is None:
            return _failure(f"decision {submission.decision_id} is not yet resolved")
        if submission.choice != decision.resolved_choice:
            return _failure(f"choice `{submission.choice}` is not the resolved choice `{decision.resolved_choice}`")

        facts = self._chunks.load_facts(chunk.chunk_id)
        if facts is None:
            return _failure(f"unknown chunk {chunk.chunk_id}")
        if derive_chunk_status(facts) in _TERMINAL_STATUSES:
            return _failure("chunk is terminal")
        latest = latest_epoch(facts)
        if latest is not None and submission.epoch != latest:
            return _failure(f"stale epoch {submission.epoch}; chunk is at {latest}")

        edge = graph.edge_for_choice(gate_node.node_id, submission.choice)
        if edge is None:
            return _failure(f"gate `{gate_node.name}` has no choice `{submission.choice}`")
        to_node_id = RESERVED_TERMINAL if edge.to_node_name == RESERVED_TERMINAL else _resolve(graph, edge.to_node_name)
        if to_node_id is None:
            return _failure(f"choice `{submission.choice}` routes to unknown node {edge.to_node_name}")

        self._chunks.record_transition(
            transition_id=mint(TRANSITION_PREFIX, self._clock),
            chunk_id=chunk.chunk_id,
            from_node_id=gate_node.node_id,
            to_node_id=to_node_id,
            choice_name=submission.choice,
            epoch=submission.epoch,
            runner_id=submission.runner_id,
            at=self._clock.now(),
            artifacts=[],  # the decision's artifacts already landed
            decision_id=submission.decision_id,
        )
        return self._respond(
            chunk, graph, gate_node, submission, to_node_id=to_node_id, run_coordinator=True, edge=edge
        )

    def _respond(
        self,
        chunk: Chunk,
        graph: Graph,
        from_node: Node,
        submission: CompletionSubmission,
        *,
        to_node_id: str,
        run_coordinator: bool,
        edge: Edge | None = None,
    ) -> ApplyResponse:
        if to_node_id == RESERVED_TERMINAL:
            return ApplyResponse(outcome=ApplyOutcome.DONE, detail="chunk reached the terminal")
        to_node = graph.node_by_id(to_node_id)
        if to_node is None:
            return _failure(f"transition target {to_node_id} is not a node")

        if to_node.executor is Executor.HUB:
            # Run the coordinator on BOTH the fresh apply and the idempotent replay
            # (``run_coordinator`` is ignored here): delivery is itself idempotent and
            # resumable (``finalize_delivery`` + the per-repo skip, D-091), so a completion
            # re-flushed after a mid-delivery hub crash RESUMES the interrupted delivery
            # rather than wedging the chunk at ``delivering`` — the deliver-crash recovery.
            self._coordinator.deliver(chunk, graph, to_node, epoch=submission.epoch)
            return ApplyResponse(
                outcome=ApplyOutcome.HUB_NODE_TAKEN,
                detail=f"hub node `{to_node.name}` took over; poll the chunk for the outcome",
            )
        if to_node.judged_by is JudgedBy.HUMAN:
            # A transition INTO a human-judged node opens a graph gate: park on a decision
            # carrying the node's choice set. Only on the real apply, never a replay.
            if run_coordinator:
                self._open_graph_gate_decision(chunk, to_node, epoch=submission.epoch)
            return ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{to_node.name}`")

        addendum = edge.prompt_addendum if edge is not None else _addendum(graph, from_node, submission.choice)
        envelope = build_node_envelope(
            chunk=chunk,
            node=to_node,
            artifacts=self._chunks.load_artifacts(chunk.chunk_id),
            epoch=submission.epoch,
            arrival_addendum=addendum,
        )
        return ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=envelope)

    def _open_graph_gate_decision(self, chunk: Chunk, gate_node: Node, *, epoch: int) -> None:
        """Open the graph gate's decision on arrival — idempotent per (chunk, node, epoch).

        The node's own choices become the decision's; no artifacts are attached (they
        arrived with the transition into the gate). A replay of the arriving transition
        never reaches here (run_coordinator=False), and the natural-key probe guards a
        double-open in any other path."""
        if self._chunks.find_decision(chunk.chunk_id, node_id=gate_node.node_id, epoch=epoch) is not None:
            return
        self._chunks.record_decision(
            decision_id=mint(DECISION_PREFIX, self._clock),
            chunk_id=chunk.chunk_id,
            node_id=gate_node.node_id,
            node_name=gate_node.name,
            epoch=epoch,
            choices=[DecisionChoice(name=c.name, description=c.description) for c in gate_node.choices],
            at=self._clock.now(),
            artifacts=[],
        )

    def _row(self, chunk: Chunk, from_node: Node, epoch: int, artifact: SubmittedArtifact) -> ArtifactRow:
        is_commit = artifact.kind is ArtifactKind.GIT_COMMIT
        data = f"{artifact.branch_name}:{artifact.commit_hash}" if is_commit else (artifact.content or "")
        return ArtifactRow(
            kind=artifact.kind,
            name=artifact.name,
            data=data,
            repo=artifact.repo if is_commit else None,
            artifact_id=mint(ARTIFACT_PREFIX, self._clock),
            chunk_id=chunk.chunk_id,
            node_id=from_node.node_id,
            node_name=from_node.name,
            epoch=epoch,
        )


def _resolve(graph: Graph, node_name: str) -> str | None:
    node = graph.node_by_name(node_name)
    return node.node_id if node is not None else None


def _addendum(graph: Graph, from_node: Node, choice: str) -> str | None:
    edge = graph.edge_for_choice(from_node.node_id, choice)
    return edge.prompt_addendum if edge is not None else None
