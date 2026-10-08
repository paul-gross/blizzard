"""Human-gate domain rules — decisions and requeue closure.

Both services hold the **write** seams their writes land on (``bzh:controller-read-only``)
and stamp time from the injected clock: :class:`DecisionService` gates a runner-configured
node and resolves first-write-wins; :class:`RequeueService` closes an escalation.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, ChunkVerb, GateDecision
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.route import IWriteChunkRouteRepository
from blizzard.hub.domain.execution.auth.commit_pointer import CommitPointerPolicy
from blizzard.hub.domain.execution.auth.produces import PRODUCES_WARN, Produces
from blizzard.hub.domain.execution.auth.proposals import ProposalPolicy
from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_WARN, RouteToken
from blizzard.hub.domain.execution.completion import (
    CompletionRefused,
    decision_choices,
    gate_refusal,
    refuse_hub_executed,
    refuse_incoherent_attempt,
    stamped_proposals,
    stored_artifacts,
)
from blizzard.hub.domain.execution.submissions import CompletionArtifact, GateSubmission
from blizzard.hub.domain.graph.model import Graph, Node
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard


@domain_model
@dataclass(frozen=True)
class DecisionSubmitResult:
    """:meth:`DecisionService.submit`'s own return — what the submission produced and its
    detail, plus the identity of the durable fact this call just wrote. ``decision_id``
    is set only on a fresh ``decisions`` row, never on a failure or an idempotent
    replay."""

    outcome: ApplyOutcome
    detail: str
    decision_id: str | None = None

    @classmethod
    def failure(cls, detail: str) -> DecisionSubmitResult:
        return cls(outcome=ApplyOutcome.FAILURE, detail=detail)

    @classmethod
    def parked(cls, gate_node: Node, decision_id: str | None = None) -> DecisionSubmitResult:
        return cls(
            outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{gate_node.name}`", decision_id=decision_id
        )


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
        exclusive: IChunkExclusiveWrites,
        retired: RetiredRunnerGuard,
        clock: IClock,
    ) -> None:
        self._facts = facts
        self._route = route
        self._decisions = decisions
        # Re-derives the current-node rule under the row lock (``bzh:store-exclusive-write``).
        self._exclusive = exclusive
        self._retired = retired
        self._clock = clock

    def submit(
        self,
        chunk: Chunk,
        graph: Graph,
        submission: GateSubmission,
        *,
        route_token_mode: str = ROUTE_TOKEN_WARN,
        produces_mode: str = PRODUCES_WARN,
    ) -> DecisionSubmitResult:
        """Runner-config gate: park the chunk on a decision instead of transitioning. A retired submitting
        runner is refused with :class:`~blizzard.hub.domain.runners.registration.RunnerRetired` first.

        Order is behavior: retired → node → facts → route token → replay → hub-executed node →
        attempt coherence → proposals → commit pointer → produces → record. Attempt coherence is re-derived under the
        chunk's row lock, ahead of the write itself."""
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
            return DecisionSubmitResult.parked(node)

        try:
            refuse_hub_executed(node)
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

        decision_id = Id.mint(IdPrefix.DECISION, self._clock).value
        artifact_ids = [Id.mint(IdPrefix.ARTIFACT, self._clock).value for _ in submission.artifacts]
        proposal_ids = [Id.mint(IdPrefix.WORK_ITEM_PROPOSAL, self._clock).value for _ in submission.proposals]
        artifact_rows = self._artifact_rows(chunk, node, submission.epoch, submission.artifacts, artifact_ids)
        proposal_rows = stamped_proposals(
            chunk.chunk_id,
            node,
            submission.epoch,
            submission.proposals,
            proposal_ids=proposal_ids,
            runner_id=submission.runner_id,
        )
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            locked = handle.facts(chunk.chunk_id)
            if locked is None:
                return DecisionSubmitResult.failure(f"unknown chunk {chunk.chunk_id}")
            try:
                refuse_incoherent_attempt(locked, graph, from_node=node, epoch=submission.epoch)
            except CompletionRefused as refused:
                return DecisionSubmitResult.failure(refused.detail)
            recorded = self._decisions.record_decision_locked(
                handle,
                decision_id=decision_id,
                chunk_id=chunk.chunk_id,
                node_id=node.node_id,
                node_name=node.name,
                epoch=submission.epoch,
                admission=EpochAdmission.CURRENT,
                claimant=Claimant(submission.runner_id, submission.lease_id),
                choices=decision_choices(node),
                at=self._clock.now(),
                artifacts=artifact_rows,
                proposals=proposal_rows,
                imposed_by_runner_id=submission.runner_id,
            )
        if isinstance(recorded, FenceRefusal):
            return DecisionSubmitResult.failure(recorded.detail)
        # `False` — a racing duplicate already opened this decision: the lost-ack replay.
        return DecisionSubmitResult.parked(node, decision_id if recorded else None)

    @staticmethod
    def _artifact_rows(
        chunk: Chunk, node: Node, epoch: int, artifacts: Sequence[CompletionArtifact], ids: Sequence[str]
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
