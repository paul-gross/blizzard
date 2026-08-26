"""Human-gate domain rules — decisions and requeue closure.

Both services hold the **write** chunk repository (``bzh:controller-read-only``) and
stamp time from the injected clock: :class:`DecisionService` gates a runner-configured
node and resolves first-write-wins; :class:`RequeueService` closes an escalation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ARTIFACT_PREFIX, DECISION_PREFIX, PROPOSAL_PREFIX, Id
from blizzard.hub.config import ROUTE_TOKEN_WARN
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.graph import Graph, Node
from blizzard.hub.domain.proposal_auth import ProposalPolicy
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.route_auth import RouteToken
from blizzard.hub.domain.work import (
    TERMINAL_STATUSES,
    Chunk,
    DecisionChoice,
    IWriteChunkRepository,
)
from blizzard.wire.completion import SubmittedArtifact, WorkItemProposal
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse


@dataclass(frozen=True)
class DecisionSubmitResult:
    """:meth:`DecisionService.submit`'s own return — the wire :class:`ApplyResponse` plus
    the identity of the durable fact this call just wrote (issue #213). ``decision_id``
    is set only on a fresh ``decisions`` row, never on a failure or an idempotent
    replay."""

    response: ApplyResponse
    decision_id: str | None = None

    @classmethod
    def failure(cls, detail: str) -> DecisionSubmitResult:
        return cls(response=ApplyResponse(outcome=ApplyOutcome.FAILURE, detail=detail))


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
    ) -> DecisionSubmitResult:
        """Runner-config gate: park the chunk on a decision instead of transitioning."""
        node = graph.node_by_id(submission.from_node_id)
        if node is None:
            return DecisionSubmitResult.failure(f"no node {submission.from_node_id} in graph {graph.graph_id}")
        if not node.choices:
            return DecisionSubmitResult.failure(f"node {node.name} has no choices to gate")

        facts = self._chunks.load_facts(chunk.chunk_id)
        if facts is None:
            return DecisionSubmitResult.failure(f"unknown chunk {chunk.chunk_id}")

        # Route-token authorization (issue #84b): ahead of the idempotent-replay probe and
        # the epoch fence, so a post-release zombie's replayed decision is rejected too.
        route = self._chunks.route_of(chunk.chunk_id)
        detail = RouteToken(
            facts=facts,
            presented=submission.route_token,
            submission_runner_id=submission.runner_id,
            route_runner_id=route.runner_id if route is not None else None,
        ).rejection(mode=route_token_mode)
        if detail is not None:
            return DecisionSubmitResult.failure(detail)

        # Idempotent replay: a decision already open at this (node, epoch) — a
        # lost-ack re-submission — returns the parked outcome without a second row.
        if self._chunks.find_decision(chunk.chunk_id, node_id=node.node_id, epoch=submission.epoch) is not None:
            return DecisionSubmitResult(
                response=ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{node.name}`")
            )

        # Proposed-work-item policy refusal (D6) — the same unconditional check
        # ``ApplyService.apply`` runs, since a runner-config gate is the fourth dispatch fork.
        policy_rejection = ProposalPolicy(node, submission.proposals).rejection()
        if policy_rejection is not None:
            return DecisionSubmitResult.failure(policy_rejection)

        if facts.status() in TERMINAL_STATUSES:
            return DecisionSubmitResult.failure("chunk is terminal")
        latest = facts.latest_epoch()
        if latest is not None and submission.epoch != latest:
            return DecisionSubmitResult.failure(f"stale epoch {submission.epoch}; chunk is at {latest}")

        decision_id = Id.mint(DECISION_PREFIX, self._clock).value
        self._chunks.record_decision(
            decision_id=decision_id,
            chunk_id=chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=submission.epoch,
            choices=[DecisionChoice(name=c.name, description=c.description) for c in node.choices],
            at=self._clock.now(),
            artifacts=[self._row(chunk, node, submission.epoch, a) for a in submission.artifacts],
            proposals=self._proposal_rows(
                chunk, node, submission.epoch, submission.proposals, runner_id=submission.runner_id
            ),
        )
        return DecisionSubmitResult(
            response=ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{node.name}`"),
            decision_id=decision_id,
        )

    def _row(self, chunk: Chunk, from_node: Node, epoch: int, artifact: SubmittedArtifact) -> ArtifactRow:
        """Twin of :meth:`~blizzard.hub.domain.apply.ApplyService._row`; the shared owner would be
        :class:`ArtifactRow`, which cannot import the wire type without a cycle."""
        is_commit = artifact.kind is ArtifactKind.GIT_COMMIT
        data = f"{artifact.branch_name}:{artifact.commit_hash}" if is_commit else (artifact.content or "")
        return ArtifactRow(
            kind=artifact.kind,
            name=artifact.name,
            data=data,
            repo=artifact.repo if is_commit else None,
            forge=artifact.forge if is_commit else None,
            artifact_id=Id.mint(ARTIFACT_PREFIX, self._clock).value,
            chunk_id=chunk.chunk_id,
            node_id=from_node.node_id,
            node_name=from_node.name,
            epoch=epoch,
        )

    def _proposal_rows(
        self, chunk: Chunk, node: Node, epoch: int, proposals: list[WorkItemProposal], *, runner_id: str
    ) -> list[WorkItemProposalRow]:
        """Twin of :meth:`~blizzard.hub.domain.apply.ApplyService._proposal_rows`."""
        return [
            WorkItemProposalRow.of(
                p,
                proposal_id=Id.mint(PROPOSAL_PREFIX, self._clock).value,
                chunk_id=chunk.chunk_id,
                node_id=node.node_id,
                node_name=node.name,
                epoch=epoch,
                ordinal=ordinal,
                runner_id=runner_id,
            )
            for ordinal, p in enumerate(proposals)
        ]

    def resolve(
        self, decision_id: str, *, choice: str, resolved_by: str, struck: Sequence[str] = ()
    ) -> ResolutionResult | None:
        """Record a person's choice, first-write-wins, striking ``struck``'s proposal ids
        in the same write. ``None`` if no such decision. A struck id naming anything but
        one of the decision's chunk's own pending, unstruck proposals raises — the same
        rejection class as an invalid ``choice``. Skipped once this decision is already
        resolved, so a retry or duplicate submission falls straight through to the CAS
        and is told who won, instead of 400ing on ids this same decision already struck."""
        decision = self._chunks.get_decision(decision_id)
        if decision is None:
            return None
        if choice not in {c.name for c in decision.choices}:
            valid = ", ".join(c.name for c in decision.choices)
            raise ValueError(f"`{choice}` is not a choice of this decision (one of: {valid})")
        if decision.resolved_choice is None:
            strikeable = {e.proposal.proposal_id for e in decision.docket if not e.struck}
            unknown = set(struck) - strikeable
            if unknown:
                raise ValueError(f"not a pending proposal of chunk {decision.chunk_id}: {', '.join(sorted(unknown))}")
        won = self._chunks.record_decision_resolution(
            decision_id, choice=choice, resolved_by=resolved_by, at=self._clock.now(), struck=struck
        )
        if won:
            return ResolutionResult(resolved=True, choice=choice, resolved_by=resolved_by)
        # Lost the CAS — report the winner so the loser is told who resolved. No strike
        # was written either: the loser's whole write, not just the choice, applies nothing.
        current = self._chunks.get_decision(decision_id)
        assert current is not None and current.resolved_choice is not None
        return ResolutionResult(resolved=False, choice=current.resolved_choice, resolved_by=current.resolved_by or "")


class RequeueService:
    """Close an open escalation by supersession — ``blizzard hub requeue``."""

    def __init__(self, *, chunks: IWriteChunkRepository, clock: IClock) -> None:
        self._chunks = chunks
        self._clock = clock

    def requeue(self, chunk_id: str) -> int:
        """Supersede the open escalation and release the route so the chunk re-derives ready.

        Raises :class:`NotEscalated` if the chunk is not ``needs_human``. Returns the
        freshly-written ``requeues.id`` (issue #213)."""
        facts = self._chunks.load_facts(chunk_id)
        if facts is None or facts.open_escalation() is None:
            raise NotEscalated(f"chunk {chunk_id} is not escalated (needs_human)")
        now = self._clock.now()
        requeue_id = self._chunks.record_requeue(chunk_id, at=now)  # supersedes the escalation
        self._chunks.record_route_released(chunk_id, at=now)  # -> ready, re-leasable at its current node
        return requeue_id
