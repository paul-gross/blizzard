"""Human-gate domain rules — decisions and requeue closure.

Two services carry this wave's human-loop writes; both hold the **write** chunk
repository (``bzh:controller-read-only``) and stamp time from the injected clock:

* :class:`DecisionService` — the runner-config gate (``POST /chunks/{id}/decisions``)
  and resolution (``POST /decisions/{id}/resolution``). A runner submits a decision in
  place of a transition for a node it was configured to gate: the choice set is
  the node's own (the hub owns the graph), and the step's artifacts commit atomically
  with the decision. Resolution is first-write-wins, like an answer. The
  *graph* gate — opening a decision when a transition lands on a human-judged node — is
  the apply rule's job (:mod:`blizzard.hub.domain.apply`), not this service.
* :class:`RequeueService` — ``POST /chunks/{id}/requeues`` closes an open escalation by
  supersession: a ``requeue.recorded`` fact supersedes the escalation and the
  route is released, so the chunk re-derives ``ready`` and re-enters FILL at its current
  node — a fresh attempt. Never a resolution fact.
"""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ARTIFACT_PREFIX, DECISION_PREFIX, mint
from blizzard.hub.config import ROUTE_TOKEN_WARN
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.route_auth import check_route_token
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
    """The outcome of a resolution attempt (first-write-wins)."""

    resolved: bool  # True on the winning write; False when already resolved
    choice: str
    resolved_by: str


class NotEscalated(Exception):
    """A requeue targeted a chunk that is not ``needs_human`` — nothing to supersede."""


class DecisionService:
    """Open runner-config gate decisions and resolve them."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def submit(
        self, chunk: Chunk, graph: Graph, submission: DecisionSubmission, *, route_token_mode: str = ROUTE_TOKEN_WARN
    ) -> ApplyResponse:
        """Runner-config gate: park the chunk on a decision instead of transitioning."""
        node = graph.node_by_id(submission.from_node_id)
        if node is None:
            return _failure(f"no node {submission.from_node_id} in graph {graph.graph_id}")
        if not node.choices:
            return _failure(f"node {node.name} has no choices to gate")

        facts = self._chunks.load_facts(chunk.chunk_id)
        if facts is None:
            return _failure(f"unknown chunk {chunk.chunk_id}")

        # Route-token authorization (issue #84b) — same order and rationale as
        # ``apply.py``'s own check: ahead of the idempotent-replay probe (a
        # post-release zombie's replayed decision is rejected too) and the epoch fence.
        route = self._chunks.route_of(chunk.chunk_id)
        detail = check_route_token(
            facts,
            presented_token=submission.route_token,
            submission_runner_id=submission.runner_id,
            route_runner_id=route.runner_id if route is not None else None,
            mode=route_token_mode,
        )
        if detail is not None:
            return _failure(detail)

        # Idempotent replay: a decision already open at this (node, epoch) — a
        # lost-ack re-submission — returns the parked outcome without a second row.
        if self._chunks.find_decision(chunk.chunk_id, node_id=node.node_id, epoch=submission.epoch) is not None:
            return ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{node.name}`")

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
        """Record a person's choice, first-write-wins. ``None`` if no such decision."""
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
        # Lost the CAS — report the winner so the loser is told who resolved.
        current = self._chunks.get_decision(decision_id)
        assert current is not None and current.resolved_choice is not None
        return ResolutionResult(resolved=False, choice=current.resolved_choice, resolved_by=current.resolved_by or "")


class RequeueService:
    """Close an open escalation by supersession — ``blizzard hub requeue``."""

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
        self._chunks.record_requeue(chunk_id, at=now)  # supersedes the escalation
        self._chunks.record_route_released(chunk_id, at=now)  # -> ready, re-leasable at its current node


def _artifact_row(
    chunk_id: str, node_id: str, node_name: str, epoch: int, artifact: SubmittedArtifact, clock: IClock
) -> ArtifactRow:
    """Compress a submitted artifact into its storage row (mirrors the apply path)."""
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
