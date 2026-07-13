"""Human-gate domain rules (D-045/D-032/D-067) — decisions and requeue closure.

Two services carry this wave's human-loop writes; both hold the **write** chunk
repository (``bzh:controller-read-only``) and stamp time from the injected clock:

* :class:`DecisionService` — the runner-config gate (``POST /chunks/{id}/decisions``)
  and resolution (``POST /decisions/{id}/resolution``). A runner submits a decision in
  place of a transition for a node it was configured to gate (D-032): the choice set is
  the node's own (the hub owns the graph), and the step's artifacts commit atomically
  with the decision (D-036). Resolution is first-write-wins, like an answer (D-045). The
  *graph* gate — opening a decision when a transition lands on a human-judged node — is
  the apply rule's job (:mod:`blizzard.hub.domain.apply`), not this service.
* :class:`RequeueService` — ``POST /chunks/{id}/requeues`` closes an open escalation by
  supersession (D-067): a ``requeue.recorded`` fact supersedes the escalation and the
  route is released, so the chunk re-derives ``ready`` and re-enters FILL at its current
  node — a fresh attempt (design/cli.md). Never a resolution fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ARTIFACT_PREFIX, DECISION_PREFIX, mint
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import (
    Chunk,
    ChunkStatus,
    DecisionChoice,
    IWriteChunkRepository,
    derive_chunk_status,
    latest_epoch,
    open_escalation,
)
from blizzard.wire.completion import SubmittedArtifact
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse

_TERMINAL_STATUSES = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})


def _failure(detail: str) -> ApplyResponse:
    return ApplyResponse(outcome=ApplyOutcome.FAILURE, detail=detail)


@dataclass(frozen=True)
class ResolutionResult:
    """The outcome of a resolution attempt (first-write-wins, D-045)."""

    resolved: bool  # True on the winning write; False when already resolved
    choice: str
    resolved_by: str


class NotEscalated(Exception):
    """A requeue targeted a chunk that is not ``needs_human`` — nothing to supersede."""


class DecisionService:
    """Open runner-config gate decisions and resolve them (D-032/D-045)."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def submit(self, chunk: Chunk, graph: Graph, submission: DecisionSubmission) -> ApplyResponse:
        """Runner-config gate: park the chunk on a decision instead of transitioning (D-032)."""
        node = graph.node_by_id(submission.from_node_id)
        if node is None:
            return _failure(f"no node {submission.from_node_id} in graph {graph.graph_id}")
        if not node.choices:
            return _failure(f"node {node.name} has no choices to gate")

        # Idempotent replay (D-045): a decision already open at this (node, epoch) — a
        # lost-ack re-submission — returns the parked outcome without a second row.
        if self._chunks.find_decision(chunk.chunk_id, node_id=node.node_id, epoch=submission.epoch) is not None:
            return ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{node.name}`")

        facts = self._chunks.load_facts(chunk.chunk_id)
        if facts is None:
            return _failure(f"unknown chunk {chunk.chunk_id}")
        if derive_chunk_status(facts) in _TERMINAL_STATUSES:
            return _failure("chunk is terminal")
        latest = latest_epoch(facts)
        if latest is not None and submission.epoch != latest:
            return _failure(f"stale epoch {submission.epoch}; chunk is at {latest}")

        self._chunks.record_decision(
            decision_id=mint(DECISION_PREFIX, self._clock),
            chunk_id=chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=submission.epoch,
            choices=[DecisionChoice(name=c.name, description=c.description) for c in node.choices],
            at=self._clock.now(),
            artifacts=[
                _artifact_row(chunk.chunk_id, node.node_id, node.name, submission.epoch, a, self._clock)
                for a in submission.artifacts
            ],
        )
        return ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{node.name}`")

    def resolve(self, decision_id: str, *, choice: str, resolved_by: str) -> ResolutionResult | None:
        """Record a person's choice, first-write-wins (D-045). ``None`` if no such decision."""
        decision = self._chunks.get_decision(decision_id)
        if decision is None:
            return None
        if choice not in {c.name for c in decision.choices}:
            valid = ", ".join(c.name for c in decision.choices)
            raise ValueError(f"`{choice}` is not a choice of this decision (one of: {valid})")
        won = self._chunks.record_decision_resolution(
            decision_id, choice=choice, resolved_by=resolved_by, at=self._clock.now()
        )
        if won:
            return ResolutionResult(resolved=True, choice=choice, resolved_by=resolved_by)
        # Lost the CAS — report the winner so the loser is told who resolved (D-045).
        current = self._chunks.get_decision(decision_id)
        assert current is not None and current.resolved_choice is not None
        return ResolutionResult(resolved=False, choice=current.resolved_choice, resolved_by=current.resolved_by or "")


class RequeueService:
    """Close an open escalation by supersession — ``blizzard hub requeue`` (D-067)."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def requeue(self, chunk_id: str) -> None:
        """Supersede the open escalation and release the route so the chunk re-derives ready.

        Raises :class:`NotEscalated` if the chunk is not ``needs_human`` — there is no
        escalation to close, so a requeue would be meaningless."""
        facts = self._chunks.load_facts(chunk_id)
        if facts is None or open_escalation(facts) is None:
            raise NotEscalated(f"chunk {chunk_id} is not escalated (needs_human)")
        now = self._clock.now()
        self._chunks.record_requeue(chunk_id, at=now)  # supersedes the escalation (D-067)
        self._chunks.record_route_released(chunk_id, at=now)  # -> ready, re-leasable at its current node


def _artifact_row(
    chunk_id: str, node_id: str, node_name: str, epoch: int, artifact: SubmittedArtifact, clock: IClock
) -> ArtifactRow:
    """Compress a submitted artifact into its storage row (mirrors the apply path, D-036)."""
    is_commit = artifact.kind is ArtifactKind.GIT_COMMIT
    data = f"{artifact.branch_name}:{artifact.commit_hash}" if is_commit else (artifact.content or "")
    return ArtifactRow(
        kind=artifact.kind,
        name=artifact.name,
        data=data,
        repo=artifact.repo if is_commit else None,
        artifact_id=mint(ARTIFACT_PREFIX, clock),
        chunk_id=chunk_id,
        node_id=node_id,
        node_name=node_name,
        epoch=epoch,
    )
