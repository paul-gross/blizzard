"""Human-gate domain rules — decisions and requeue closure.

Both services hold the **write** seams their writes land on (``bzh:controller-read-only``)
and stamp time from the injected clock: :class:`DecisionService` gates a runner-configured
node and resolves first-write-wins; :class:`RequeueService` closes an escalation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import ARTIFACT_PREFIX, DECISION_PREFIX, WORK_ITEM_PROPOSAL_PREFIX, Id
from blizzard.foundation.roles import domain_model
from blizzard.hub.config import PRODUCES_WARN, ROUTE_TOKEN_WARN
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb, GateDecision
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.execution.auth.commit_pointer import CommitPointerPolicy
from blizzard.hub.domain.execution.auth.produces import Produces
from blizzard.hub.domain.execution.auth.proposals import ProposalPolicy
from blizzard.hub.domain.execution.auth.route import RouteToken
from blizzard.hub.domain.execution.completion import (
    CompletionRefused,
    decision_choices,
    gate_refusal,
    refuse_incoherent_attempt,
    stamped_proposals,
    stored_artifacts,
)
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard
from blizzard.wire.completion import SubmittedArtifact
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse


@domain_model
@dataclass(frozen=True)
class DecisionSubmitResult:
    """:meth:`DecisionService.submit`'s own return — the wire :class:`ApplyResponse` plus
    the identity of the durable fact this call just wrote. ``decision_id``
    is set only on a fresh ``decisions`` row, never on a failure or an idempotent
    replay."""

    response: ApplyResponse
    decision_id: str | None = None

    @classmethod
    def failure(cls, detail: str) -> DecisionSubmitResult:
        return cls(response=ApplyResponse(outcome=ApplyOutcome.FAILURE, detail=detail))


@domain_model
@dataclass(frozen=True)
class ResolutionResult:
    """The outcome of a resolution attempt (first-write-wins)."""

    resolved: bool  # True on the winning write; False when already resolved
    choice: str
    resolved_by: str


class NotEscalated(Exception):
    """A requeue targeted a chunk that is not ``needs_human`` — nothing to supersede
    (:attr:`ChunkVerb.REQUEUE`)."""


class DecisionService:
    """Open runner-config gate decisions and resolve them."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        route: IWriteChunkRouteRepository,
        decisions: IWriteChunkDecisionsRepository,
        retired: RetiredRunnerGuard,
        clock: IClock,
    ) -> None:
        self._facts = facts
        self._route = route
        self._decisions = decisions
        self._retired = retired
        self._clock = clock

    def submit(
        self,
        chunk: Chunk,
        graph: Graph,
        submission: DecisionSubmission,
        *,
        route_token_mode: str = ROUTE_TOKEN_WARN,
        produces_mode: str = PRODUCES_WARN,
    ) -> DecisionSubmitResult:
        """Runner-config gate: park the chunk on a decision instead of transitioning. A retired
        submitting runner is refused with :class:`RunnerRetired` before anything lands.

        Order is behavior: retired → node → facts → route token → replay → attempt coherence →
        proposals → commit pointer → produces → record."""
        self._retired.refuse_if_retired(submission.runner_id, action="decision")
        try:
            node = gate_refusal(graph, submission.from_node_id)
        except CompletionRefused as refused:
            return DecisionSubmitResult.failure(refused.detail)

        facts = self._facts.load_facts(chunk.chunk_id)
        if facts is None:
            return DecisionSubmitResult.failure(f"unknown chunk {chunk.chunk_id}")

        # Route-token authorization: ahead of the idempotent-replay probe and
        # the epoch fence, so a post-release zombie's replayed decision is rejected too.
        route = self._route.route_of(chunk.chunk_id)
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
        if self._decisions.find_decision(chunk.chunk_id, node_id=node.node_id, epoch=submission.epoch) is not None:
            return DecisionSubmitResult(
                response=ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{node.name}`")
            )

        try:
            refuse_incoherent_attempt(facts, graph, from_node=node, epoch=submission.epoch)
            # The same unconditional policies `ApplyService.apply` runs — a runner-config gate is a
            # dispatch fork too, and the step's artifacts land here, so the produces backstop runs.
            for rejection in (
                ProposalPolicy(node, submission.proposals).rejection(),
                CommitPointerPolicy(submission.artifacts).rejection(),
                Produces(node, submission.artifacts).rejection(mode=produces_mode),
            ):
                if rejection is not None:
                    raise CompletionRefused(rejection)
        except CompletionRefused as refused:
            return DecisionSubmitResult.failure(refused.detail)

        decision_id = Id.mint(DECISION_PREFIX, self._clock).value
        artifact_ids = [Id.mint(ARTIFACT_PREFIX, self._clock).value for _ in submission.artifacts]
        proposal_ids = [Id.mint(WORK_ITEM_PROPOSAL_PREFIX, self._clock).value for _ in submission.proposals]
        refusal = self._decisions.record_decision(
            decision_id=decision_id,
            chunk_id=chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=submission.epoch,
            admission=EpochAdmission.CURRENT,
            claimant=Claimant(submission.runner_id, submission.lease_id),
            choices=decision_choices(node),
            at=self._clock.now(),
            artifacts=self._artifact_rows(chunk, node, submission.epoch, submission.artifacts, artifact_ids),
            proposals=stamped_proposals(
                chunk.chunk_id,
                node,
                submission.epoch,
                submission.proposals,
                proposal_ids=proposal_ids,
                runner_id=submission.runner_id,
            ),
            imposed_by_runner_id=submission.runner_id,
        )
        if refusal is not None:
            return DecisionSubmitResult.failure(refusal.detail)
        return DecisionSubmitResult(
            response=ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{node.name}`"),
            decision_id=decision_id,
        )

    @staticmethod
    def _artifact_rows(
        chunk: Chunk, node: Node, epoch: int, artifacts: Sequence[SubmittedArtifact], ids: Sequence[str]
    ) -> list[StoredArtifact]:
        return stored_artifacts(chunk.chunk_id, node, epoch, artifacts, artifact_ids=ids)

    def resolve(
        self, decision: GateDecision, *, choice: str, resolved_by: str, struck: Sequence[str] = ()
    ) -> ResolutionResult:
        """Record a choice, first-write-wins, striking ``struck``'s proposal ids in the same
        write. Takes the already-resolved decision (``bzh:domain-takes-objects``), not a bare
        ``decision_id``. :meth:`GateDecision.require_resolvable` decides the refusals; the
        chunk's derived status is read here for it."""
        facts = ChunkFacts.or_default(self._facts.load_facts(decision.chunk_id))
        decision.require_resolvable(choice=choice, struck=struck, chunk_status=facts.status())
        won = self._decisions.record_decision_resolution(
            decision.decision_id, choice=choice, resolved_by=resolved_by, at=self._clock.now(), struck=struck
        )
        if won:
            return ResolutionResult(resolved=True, choice=choice, resolved_by=resolved_by)
        # Lost the CAS — report the winner so the loser is told who resolved. No strike
        # was written either: the loser's whole write, not just the choice, applies nothing.
        current = self._decisions.get_decision(decision.decision_id)
        assert current is not None and current.resolved_choice is not None
        return ResolutionResult(resolved=False, choice=current.resolved_choice, resolved_by=current.resolved_by or "")


class RequeueService:
    """Close an open escalation by supersession — ``blizzard hub requeue``."""

    def __init__(
        self,
        *,
        movement: IWriteChunkMovementRepository,
        route: IWriteChunkRouteRepository,
        exclusive: IChunkExclusiveWrites,
        clock: IClock,
    ) -> None:
        self._movement = movement
        self._route = route
        # The locked-transaction seam (``bzh:store-exclusive-write``): the route release
        # races claim's own ``route_of`` read, so it and the requeue fact it always
        # accompanies both land inside the same row-locked transaction.
        self._exclusive = exclusive
        self._clock = clock

    def requeue(self, chunk: Chunk) -> int:
        """Supersede the open escalation and release the route so the chunk re-derives ready.
        Takes the loaded chunk (``bzh:domain-takes-objects``). Raises :class:`NotEscalated`
        if the chunk is not ``needs_human`` — re-derived fresh under the row lock
        (``bzh:store-exclusive-write``), never from a pre-lock snapshot, so a concurrent
        requeue (or superseding fact) cannot be raced past this guard. Raises
        :class:`ChunkNotFound` for a chunk gone under the lock. Returns the freshly-written
        ``requeues.id``."""
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            facts = handle.facts(chunk.chunk_id)
            if facts is None:
                raise ChunkNotFound(chunk.chunk_id)
            if not facts.admits(ChunkVerb.REQUEUE):
                raise NotEscalated(f"chunk {chunk.chunk_id} is not escalated (needs_human)")
            now = self._clock.now()
            requeue_id = self._movement.record_requeue_locked(handle, chunk.chunk_id, at=now)  # supersedes escalation
            # A detached chunk holds no route: there is nothing to release.
            if handle.route_of(chunk.chunk_id) is not None:
                self._route.record_route_released_locked(handle, chunk.chunk_id, at=now)  # -> ready, re-leasable
            return requeue_id
