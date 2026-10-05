"""Completion apply — the advancement checkpoint.

One node-step's completion is applied here. The write is **atomic**, **epoch-fenced**,
and **idempotent** — and the idempotency probe runs **before** the terminal check. A
transition **into** a human-judged node opens a decision and parks; leaving one is legal
only as the resolving transition."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import (
    ARTIFACT_PREFIX,
    DECISION_PREFIX,
    MIGRATION_PREFIX,
    TRANSITION_PREFIX,
    WORK_ITEM_PROPOSAL_PREFIX,
    Id,
)
from blizzard.foundation.roles import domain_model
from blizzard.hub.config import PRODUCES_WARN, ROUTE_TOKEN_WARN
from blizzard.hub.delivery.hub_node import HubNodeExecutor
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, WorkRefLabel
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository
from blizzard.hub.domain.chunk.proposals import StampedWorkItemProposal
from blizzard.hub.domain.execution.auth.commit_pointer import CommitPointerPolicy
from blizzard.hub.domain.execution.auth.proposals import ProposalPolicy
from blizzard.hub.domain.execution.auth.route import RouteToken
from blizzard.hub.domain.execution.completion import (
    CompletionPlan,
    CompletionRefused,
    Landing,
    MigrationTargets,
    NextStep,
    NextStepKind,
    ReplayedMigration,
    UnresolvableTarget,
    decision_choices,
    refuse_hub_executed,
    refuse_incoherent_attempt,
    replayed_migration,
    stamped_proposals,
    stored_artifacts,
)
from blizzard.hub.domain.execution.envelope import Arrival, Envelope
from blizzard.hub.domain.graph.model import Edge, Graph, Node
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard
from blizzard.wire.completion import CompletionSubmission, SubmittedArtifact, WorkItemProposal
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeEnvelope

# The cross-graph migration crash window (``bzh:crash-point-registry``): the whole
# migration is committed but its response is not; the replayed completion re-derives it.
_CP_MIGRATE_AFTER_RECORD = crashpoint(
    "migrate.after-record.before-response",
    "migration recorded (graph/model re-pinned, route released unless hub-landing, artifacts committed);"
    " response not yet returned",
)


@domain_model
@dataclass(frozen=True)
class ApplyResult:
    """:meth:`ApplyService.apply`'s own return — the wire :class:`ApplyResponse` plus the
    identity of the durable fact this call itself just wrote. At most one of
    the two is ever set, and only on a genuinely fresh write. Not a wire type."""

    response: ApplyResponse
    transition_id: str | None = None
    migration_id: str | None = None

    @property
    def fresh_migration(self) -> bool:
        """Whether this call itself migrated the chunk — a replay carries no ``migration_id``."""
        return self._migrated_freshly()

    def _migrated_freshly(self) -> bool:
        return self.response.outcome is ApplyOutcome.MIGRATED and self.migration_id is not None

    @classmethod
    def failure(cls, detail: str) -> ApplyResult:
        return cls(response=ApplyResponse(outcome=ApplyOutcome.FAILURE, detail=detail))

    @classmethod
    def replayed(cls, replay: ReplayedMigration, *, epoch: int) -> ApplyResult:
        """The answer to a completion whose migration already landed at its (node, epoch)."""
        if replay is ReplayedMigration.SUPERSEDED_BY_RESTART:
            return cls.failure(f"superseded by a restart at epoch {epoch}")
        if replay is ReplayedMigration.HUB_NODE_TAKEN:
            return cls.hub_node_taken_replay()
        return cls.migrated_replay()

    @classmethod
    def done(cls, transition_id: str | None) -> ApplyResult:
        return cls(
            response=ApplyResponse(outcome=ApplyOutcome.DONE, detail="chunk reached the terminal"),
            transition_id=transition_id,
        )

    @classmethod
    def advance(cls, envelope: NodeEnvelope, transition_id: str | None) -> ApplyResult:
        return cls(
            response=ApplyResponse(outcome=ApplyOutcome.NEXT, next_envelope=envelope), transition_id=transition_id
        )

    @classmethod
    def parked(cls, gate_node: Node, transition_id: str | None) -> ApplyResult:
        return cls(
            response=ApplyResponse(outcome=ApplyOutcome.PARKED_AT_GATE, detail=f"parked at gate `{gate_node.name}`"),
            transition_id=transition_id,
        )

    @classmethod
    def escalated(cls, target_graph_name: str | None) -> ApplyResult:
        """An unresolved cross-graph target's park — ``FAILURE`` would requeue and supersede the
        escalation this answers."""
        return cls(
            response=ApplyResponse(
                outcome=ApplyOutcome.PARKED_AT_GATE,
                detail=f"cross-graph target `{target_graph_name}` did not resolve; chunk escalated for a human",
            )
        )

    @classmethod
    def taken_over(cls, to_node: Node, transition_id: str | None) -> ApplyResult:
        return cls(
            response=ApplyResponse(
                outcome=ApplyOutcome.HUB_NODE_TAKEN,
                detail=f"hub node `{to_node.name}` took over; poll the chunk for the outcome",
            ),
            transition_id=transition_id,
        )

    @classmethod
    def landed_on_hub(cls, landed_node: Node, migration_id: str | None) -> ApplyResult:
        return cls(
            response=ApplyResponse(
                outcome=ApplyOutcome.HUB_NODE_TAKEN,
                detail=f"migration landed on hub node `{landed_node.name}`; poll the chunk for the outcome",
            ),
            migration_id=migration_id,
        )

    @classmethod
    def migrated(cls, from_node: Node, target_graph: Graph, migration_id: str | None) -> ApplyResult:
        return cls(
            response=ApplyResponse(
                outcome=ApplyOutcome.MIGRATED,
                detail=f"node `{from_node.name}` migrated the chunk to graph `{target_graph.name}`; re-queued",
            ),
            migration_id=migration_id,
        )

    @classmethod
    def migrated_replay(cls) -> ApplyResult:
        """A lost-ack re-flush of a **runner-landing** migration that already landed.
        Carries no node/graph detail: the migration re-pinned the graph, so the natural-key probe
        alone (not a graph lookup) resolves the replay. No fresh fact, so no ``migration_id``."""
        return cls(response=ApplyResponse(outcome=ApplyOutcome.MIGRATED, detail="chunk already migrated (replay)"))

    @classmethod
    def hub_node_taken_replay(cls) -> ApplyResult:
        """A lost-ack re-flush of a completion whose migration landed on a **hub-executed** node.
        Distinct from :meth:`migrated_replay` because a hub landing **retained** the
        route, which a ``MIGRATED`` reply would release (pinned by tests/test_migration_apply.py)."""
        return cls(
            response=ApplyResponse(
                outcome=ApplyOutcome.HUB_NODE_TAKEN, detail="chunk migrated onto a hub node (replay)"
            )
        )


class ApplyService:
    """Apply a node-step completion to a chunk, fenced and idempotent. Orchestration only: it
    reads, probes for replays, asks the completion rules what to do, and records it."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        movement: IWriteChunkMovementRepository,
        decisions: IWriteChunkDecisionsRepository,
        escalations: IWriteChunkEscalationsRepository,
        route: IReadChunkRouteRepository,
        artifacts: IReadChunkArtifactsRepository,
        retired: RetiredRunnerGuard,
        clock: IClock,
        hub_node_executor: HubNodeExecutor,
        label: WorkRefLabel,
    ) -> None:
        self._facts = facts
        self._movement = movement
        self._decisions = decisions
        self._escalations = escalations
        self._route = route
        self._artifacts = artifacts
        self._retired = retired
        self._clock = clock
        self._hub_node_executor = hub_node_executor
        self._label = label

    def apply(
        self,
        chunk: Chunk,
        graph: Graph,
        submission: CompletionSubmission,
        *,
        route_token_mode: str = ROUTE_TOKEN_WARN,
        produces_mode: str = PRODUCES_WARN,
        targets: MigrationTargets | None = None,
    ) -> ApplyResult:
        """Apply a completion. ``targets`` arrive pre-resolved — each ``None`` meaning "names no
        enabled graph" — so this holds no graph repo of its own (``bzh:domain-takes-objects``).

        Order is behavior: retired → facts → migration replay → route token → from node →
        transition replay → attempt coherence → proposals → commit pointer → plan → record."""
        targets = targets or MigrationTargets(cross_graph=None, intended=None, follow_latest=None)
        self._retired.refuse_if_retired(submission.runner_id, action="completion")
        facts = self._facts.load_facts(chunk.chunk_id)
        if facts is None:
            return ApplyResult.failure(f"unknown chunk {chunk.chunk_id}")

        # Probed by natural key ahead of the graph lookup and the route-token check:
        # a migration re-pins the graph and releases the route a replay presents.
        if self._movement.accepted_migration(
            chunk.chunk_id, from_node_id=submission.from_node_id, epoch=submission.epoch
        ):
            replay = replayed_migration(facts, from_node_id=submission.from_node_id, epoch=submission.epoch)
            return ApplyResult.replayed(replay, epoch=submission.epoch)

        # Route-token authorization — ordered ahead of the replay probe and the
        # epoch fence, so a post-release zombie's replay is rejected as a fresh one is.
        rejection = self._check_route_token(chunk, facts, submission, route_token_mode=route_token_mode)
        if rejection is not None:
            return rejection

        from_node = graph.node_by_id(submission.from_node_id)
        if from_node is None:
            return ApplyResult.failure(f"no node {submission.from_node_id} in graph {graph.graph_id}")

        # Idempotent replay first: a completion already applied at this (node, epoch)
        # returns its original outcome — even once the chunk is terminal.
        replayed = self._movement.accepted_transition_target(
            chunk.chunk_id, from_node_id=submission.from_node_id, epoch=submission.epoch
        )
        if replayed is not None:
            return self._respond(chunk, graph, from_node, submission, to_node_id=replayed, is_fresh_apply=False)

        try:
            refuse_hub_executed(from_node)
            refuse_incoherent_attempt(facts, graph, from_node=from_node, epoch=submission.epoch)
            # Unconditional and ahead of every dispatch fork, so none carries a proposal past a
            # node that never declared the policy, nor a `git_commit` missing a repo, branch, or hash.
            for policy_rejection in (
                ProposalPolicy(from_node, submission.proposals).rejection(),
                CommitPointerPolicy(submission.artifacts).rejection(),
            ):
                if policy_rejection is not None:
                    raise CompletionRefused(policy_rejection)
            if submission.decision_id is not None:
                # A gate-resolving transition — a graph gate (human node) or a runner-config gate.
                decision = self._decisions.get_decision(submission.decision_id)
                plan = CompletionPlan.resolving(graph, from_node, submission, decision, chunk=chunk)
            else:
                open_gate = self._decisions.find_decision(
                    chunk.chunk_id, node_id=from_node.node_id, epoch=submission.epoch
                )
                plan = CompletionPlan.plain(
                    graph, from_node, submission, open_gate=open_gate, produces_mode=produces_mode
                )
        except CompletionRefused as refused:
            return ApplyResult.failure(refused.detail)

        # A gate's resolving transition carries no artifacts or proposals: they landed with
        # the decision, and threading `decision_id` through keeps the gate from staying live.
        resolving = submission.decision_id is not None
        artifacts = [] if resolving else submission.artifacts
        proposals = [] if resolving else submission.proposals
        if plan.migrates:
            return self._migrate_across(chunk, facts, from_node, submission, plan.edge, targets, artifacts, proposals)
        assert plan.to_node_id is not None
        # The transition-time consult — after every rejection and before `record_transition`,
        # so a firing intent or follow-latest drift writes no transition row of its own.
        landing = Landing.consult(chunk, plan.edge, targets)
        if landing is not None:
            return self._land_migration(chunk, from_node, submission, landing, submission.artifacts, proposals)
        return self._transition(chunk, graph, from_node, submission, plan, artifacts, proposals)

    def _transition(
        self,
        chunk: Chunk,
        graph: Graph,
        from_node: Node,
        submission: CompletionSubmission,
        plan: CompletionPlan,
        artifacts: list[SubmittedArtifact],
        proposals: list[WorkItemProposal],
    ) -> ApplyResult:
        assert plan.to_node_id is not None
        fresh_transition_id = Id.mint(TRANSITION_PREFIX, self._clock).value
        refusal = self._movement.record_transition(
            transition_id=fresh_transition_id,
            chunk_id=chunk.chunk_id,
            from_node_id=from_node.node_id,
            to_node_id=plan.to_node_id,
            choice_name=submission.choice,
            epoch=submission.epoch,
            admission=EpochAdmission.CURRENT,
            claimant=Claimant(submission.runner_id, submission.lease_id),
            runner_id=submission.runner_id,
            at=self._clock.now(),
            artifacts=self._artifact_rows(chunk, from_node, submission.epoch, artifacts),
            proposals=self._proposal_rows(chunk, from_node, submission, proposals),
            decision_id=submission.decision_id,
        )
        if refusal is not None:
            return ApplyResult.failure(refusal.detail)
        return self._respond(
            chunk,
            graph,
            from_node,
            submission,
            to_node_id=plan.to_node_id,
            is_fresh_apply=True,
            edge=plan.edge,
            transition_id=fresh_transition_id,
        )

    def _migrate_across(
        self,
        chunk: Chunk,
        facts: ChunkFacts,
        from_node: Node,
        submission: CompletionSubmission,
        edge: Edge,
        targets: MigrationTargets,
        artifacts: list[SubmittedArtifact],
        proposals: list[WorkItemProposal],
    ) -> ApplyResult:
        """Take a cross-graph edge — land on its resolved target, or escalate once per epoch. An
        unresolved target answers ``PARKED_AT_GATE``: ``FAILURE`` would requeue and supersede it."""
        if targets.cross_graph is not None:
            landing = Landing.authored(edge, targets.cross_graph, from_node)
            return self._land_migration(chunk, from_node, submission, landing, artifacts, proposals)
        if not facts.escalated_at(submission.epoch):
            draft = UnresolvableTarget.of(edge)
            # Hub-authored: no runner runtime dir to compose a wrapped takeover command from.
            escalated = self._escalations.record_escalation(
                chunk.chunk_id,
                epoch=submission.epoch,
                admission=EpochAdmission.CURRENT,
                claimant=Claimant(submission.runner_id, submission.lease_id),
                takeover_command=draft.takeover_command,
                at=self._clock.now(),
                decision_id=submission.decision_id,
                cause=draft.cause,
                detail=draft.detail,
            )
            if isinstance(escalated, FenceRefusal):
                return ApplyResult.failure(escalated.detail)
        return ApplyResult.escalated(edge.target_graph)

    def _land_migration(
        self,
        chunk: Chunk,
        from_node: Node,
        submission: CompletionSubmission,
        landing: Landing,
        artifacts: list[SubmittedArtifact],
        proposals: list[WorkItemProposal],
    ) -> ApplyResult:
        """Record the migration atomically (fact + re-pin + artifacts + proposals + route
        release/retain + intent clear), then govern by the landed node's executor."""
        recorded = self._movement.record_migration(
            chunk.chunk_id,
            from_node_id=from_node.node_id,
            from_graph_id=from_node.graph_id,
            to_graph_id=landing.graph.graph_id,
            landed_node_id=landing.node_id,
            choice_name=submission.choice,
            decision_id=submission.decision_id,
            model=landing.model,
            source=landing.source,
            epoch=submission.epoch,
            admission=EpochAdmission.CURRENT,
            claimant=Claimant(submission.runner_id, submission.lease_id),
            at=self._clock.now(),
            artifacts=self._artifact_rows(chunk, from_node, submission.epoch, artifacts),
            proposals=self._proposal_rows(chunk, from_node, submission, proposals),
            release_route=landing.releases_route,
            clear_intent=landing.clear_intent,
            migration_id=Id.mint(MIGRATION_PREFIX, self._clock).value,
        )
        if isinstance(recorded, FenceRefusal):
            return ApplyResult.failure(recorded.detail)
        _CP_MIGRATE_AFTER_RECORD.reached()
        landed_node = landing.node
        if not landing.releases_route:
            assert landed_node is not None
            self._hub_node_executor.run(chunk, landing.graph, landed_node, epoch=submission.epoch)
            return ApplyResult.landed_on_hub(landed_node, recorded)
        return ApplyResult.migrated(from_node, landing.graph, recorded)

    def _respond(
        self,
        chunk: Chunk,
        graph: Graph,
        from_node: Node,
        submission: CompletionSubmission,
        *,
        to_node_id: str,
        is_fresh_apply: bool,
        edge: Edge | None = None,
        transition_id: str | None = None,
    ) -> ApplyResult:
        """Perform the next step's side effects. ``transition_id`` is the fresh row on a fresh
        apply, ``None`` on a replay; every branch carries it through."""
        step = NextStep.of(graph, to_node_id)
        if step.kind is NextStepKind.DONE:
            return ApplyResult.done(transition_id)
        if step.node is None:
            return ApplyResult.failure(f"transition target {to_node_id} is not a node")
        if step.kind is NextStepKind.HUB_TAKES:
            # Run on BOTH the fresh apply and the replay: the executor is idempotent and
            # resumable, so a re-flush resumes an interrupted run.
            self._hub_node_executor.run(chunk, graph, step.node, epoch=submission.epoch)
            return ApplyResult.taken_over(step.node, transition_id)
        if step.kind is NextStepKind.GATE:
            # Only the real apply opens the gate's decision, never a replay.
            if is_fresh_apply:
                self._open_graph_gate_decision(
                    chunk,
                    step.node,
                    epoch=submission.epoch,
                    claimant=Claimant(submission.runner_id, submission.lease_id),
                )
            return ApplyResult.parked(step.node, transition_id)
        arrival = Arrival(edge) if edge is not None else Arrival.of_choice(graph, from_node, submission.choice)
        envelope = Envelope(
            chunk=chunk,
            graph=graph,
            node=step.node,
            artifacts=self._artifacts.load_artifacts(chunk.chunk_id),
            epoch=submission.epoch,
            arrival_addendum=arrival.addendum,
            label=self._label,
        )
        return ApplyResult.advance(envelope.wire, transition_id)

    def _open_graph_gate_decision(self, chunk: Chunk, gate_node: Node, *, epoch: int, claimant: Claimant) -> None:
        """Open the graph gate's decision on arrival — idempotent per (chunk, node, epoch) by the
        natural-key probe. No artifacts attach: they arrived with the transition into the gate."""
        if self._decisions.find_decision(chunk.chunk_id, node_id=gate_node.node_id, epoch=epoch) is not None:
            return
        # A refusal — the chunk was stopped or restarted since the arrival was recorded —
        # leaves the decision unopened: it would gate a superseded visit.
        self._decisions.record_decision(
            decision_id=Id.mint(DECISION_PREFIX, self._clock).value,
            chunk_id=chunk.chunk_id,
            node_id=gate_node.node_id,
            node_name=gate_node.name,
            epoch=epoch,
            admission=EpochAdmission.CURRENT,
            claimant=claimant,
            choices=decision_choices(gate_node),
            at=self._clock.now(),
            artifacts=[],
            proposals=[],
            imposed_by_runner_id=None,
        )

    def _check_route_token(
        self, chunk: Chunk, facts: ChunkFacts, submission: CompletionSubmission, *, route_token_mode: str
    ) -> ApplyResult | None:
        route = self._route.route_of(chunk.chunk_id)
        detail = RouteToken(
            facts=facts,
            presented=submission.route_token,
            submission_runner_id=submission.runner_id,
            route_runner_id=route.runner_id if route is not None else None,
        ).rejection(mode=route_token_mode)
        return ApplyResult.failure(detail) if detail is not None else None

    def _artifact_rows(
        self, chunk: Chunk, node: Node, epoch: int, artifacts: list[SubmittedArtifact]
    ) -> list[StoredArtifact]:
        ids = [Id.mint(ARTIFACT_PREFIX, self._clock).value for _ in artifacts]
        return stored_artifacts(chunk.chunk_id, node, epoch, artifacts, artifact_ids=ids)

    def _proposal_rows(
        self, chunk: Chunk, node: Node, submission: CompletionSubmission, proposals: list[WorkItemProposal]
    ) -> list[StampedWorkItemProposal]:
        ids = [Id.mint(WORK_ITEM_PROPOSAL_PREFIX, self._clock).value for _ in proposals]
        return stamped_proposals(
            chunk.chunk_id, node, submission.epoch, proposals, proposal_ids=ids, runner_id=submission.runner_id
        )
