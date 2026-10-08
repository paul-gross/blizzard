"""Completion apply — the advancement checkpoint.

One node-step's completion is applied here. The write is **atomic**, **epoch-fenced**,
and **idempotent** — and the idempotency probe runs **before** the terminal check. A
transition **into** a human-judged node opens a decision and parks; leaving one is legal
only as the resolving transition."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.node_steps import ApplyOutcome
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, WorkRefLabel
from blizzard.hub.domain.chunk.ports.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunk.ports.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunk.ports.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunk.ports.exclusive import IChunkExclusiveWrites
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import Claimant, EpochAdmission, FenceRefusal
from blizzard.hub.domain.chunk.ports.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunk.ports.route import IReadChunkRouteRepository
from blizzard.hub.domain.chunk.proposals import ItemProposal, StampedWorkItemProposal
from blizzard.hub.domain.execution.auth.commit_pointer import CommitPointerPolicy
from blizzard.hub.domain.execution.auth.produces import PRODUCES_WARN
from blizzard.hub.domain.execution.auth.proposals import ProposalPolicy
from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_WARN, RouteToken
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
    re_escalates,
    refuse_hub_executed,
    refuse_incoherent_attempt,
    replayed_migration,
    stamped_proposals,
    stored_artifacts,
)
from blizzard.hub.domain.execution.envelope import Arrival, Envelope
from blizzard.hub.domain.execution.submissions import Completion, CompletionArtifact
from blizzard.hub.domain.graph.model import Edge, Graph, Node
from blizzard.hub.domain.runners.registration import RetiredRunnerGuard

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
    """:meth:`ApplyService.apply`'s own return — what the apply produced, the envelope the runner
    continues with on ``NEXT`` or the detail of any other outcome, and the identity of the durable
    fact this call itself just wrote. At most one of the two ids is ever set, and only on a
    genuinely fresh write."""

    outcome: ApplyOutcome
    detail: str | None = None
    envelope: Envelope | None = None
    transition_id: str | None = None
    migration_id: str | None = None

    @property
    def fresh_migration(self) -> bool:
        """Whether this call itself migrated the chunk — a replay carries no ``migration_id``."""
        return self._migrated_freshly()

    def _migrated_freshly(self) -> bool:
        return self.outcome is ApplyOutcome.MIGRATED and self.migration_id is not None

    @classmethod
    def failure(cls, detail: str) -> ApplyResult:
        return cls(outcome=ApplyOutcome.FAILURE, detail=detail)

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
        return cls(outcome=ApplyOutcome.DONE, detail="chunk reached the terminal", transition_id=transition_id)

    @classmethod
    def advance(cls, envelope: Envelope, transition_id: str | None) -> ApplyResult:
        return cls(outcome=ApplyOutcome.NEXT, envelope=envelope, transition_id=transition_id)

    @classmethod
    def parked(cls, gate_node: Node, transition_id: str | None) -> ApplyResult:
        return cls(
            outcome=ApplyOutcome.PARKED_AT_GATE,
            detail=f"parked at gate `{gate_node.name}`",
            transition_id=transition_id,
        )

    @classmethod
    def escalated(cls, target_graph_name: str | None) -> ApplyResult:
        """An unresolved cross-graph target's park — ``FAILURE`` would requeue and supersede the
        escalation this answers."""
        return cls(
            outcome=ApplyOutcome.PARKED_AT_GATE,
            detail=f"cross-graph target `{target_graph_name}` did not resolve; chunk escalated for a human",
        )

    @classmethod
    def taken_over(cls, to_node: Node, transition_id: str | None) -> ApplyResult:
        return cls(
            outcome=ApplyOutcome.HUB_NODE_TAKEN,
            detail=f"hub node `{to_node.name}` took over; poll the chunk for the outcome",
            transition_id=transition_id,
        )

    @classmethod
    def landed_on_hub(cls, landed_node: Node, migration_id: str | None) -> ApplyResult:
        return cls(
            outcome=ApplyOutcome.HUB_NODE_TAKEN,
            detail=f"migration landed on hub node `{landed_node.name}`; poll the chunk for the outcome",
            migration_id=migration_id,
        )

    @classmethod
    def migrated(cls, from_node: Node, target_graph: Graph, migration_id: str | None) -> ApplyResult:
        return cls(
            outcome=ApplyOutcome.MIGRATED,
            detail=f"node `{from_node.name}` migrated the chunk to graph `{target_graph.name}`; re-queued",
            migration_id=migration_id,
        )

    @classmethod
    def migrated_replay(cls) -> ApplyResult:
        """A lost-ack re-flush of a **runner-landing** migration that already landed.
        Carries no node/graph detail: the migration re-pinned the graph, so the natural-key probe
        alone (not a graph lookup) resolves the replay. No fresh fact, so no ``migration_id``."""
        return cls(outcome=ApplyOutcome.MIGRATED, detail="chunk already migrated (replay)")

    @classmethod
    def hub_node_taken_replay(cls) -> ApplyResult:
        """A lost-ack re-flush of a completion whose migration landed on a **hub-executed** node.
        Distinct from :meth:`migrated_replay` because a hub landing **retained** the
        route, which a ``MIGRATED`` reply would release (pinned by tests/test_migration_apply.py)."""
        return cls(outcome=ApplyOutcome.HUB_NODE_TAKEN, detail="chunk migrated onto a hub node (replay)")


@domain_model
@dataclass(frozen=True)
class _Raced:
    """A movement a concurrent submission already recorded at this submission's ``(node, epoch)``:
    the transition's target, or the migration's replay class."""

    transition_to: str | None = None
    migration: ReplayedMigration | None = None


class IHubNodeExecutor(Protocol):
    """Runs a hub-executed node's ``run:`` list once; the result is the executor's own and unread here."""

    def run(self, chunk: Chunk, graph: Graph, node: Node, *, epoch: int) -> object: ...


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
        exclusive: IChunkExclusiveWrites,
        route: IReadChunkRouteRepository,
        artifacts: IReadChunkArtifactsRepository,
        retired: RetiredRunnerGuard,
        clock: IClock,
        hub_node_executor: IHubNodeExecutor,
        label: WorkRefLabel,
    ) -> None:
        self._facts = facts
        self._movement = movement
        self._decisions = decisions
        self._escalations = escalations
        # Re-derives the current-node rule under the row lock (``bzh:store-exclusive-write``).
        self._exclusive = exclusive
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
        submission: Completion,
        *,
        route_token_mode: str = ROUTE_TOKEN_WARN,
        produces_mode: str = PRODUCES_WARN,
        targets: MigrationTargets | None = None,
    ) -> ApplyResult:
        """Apply a completion. ``targets`` arrive pre-resolved — each ``None`` meaning "names no
        enabled graph" — so this holds no graph repo of its own (``bzh:domain-takes-objects``).

        Order is behavior: retired → facts → migration replay → route token → from node →
        transition replay → attempt coherence → proposals → commit pointer → plan → record. Attempt
        coherence is re-derived under the chunk's row lock, ahead of the write itself."""
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
            refuse_incoherent_attempt(
                facts,
                graph,
                from_node=from_node,
                epoch=submission.epoch,
                re_escalating=re_escalates(
                    facts,
                    graph.edge_for_choice(from_node.node_id, submission.choice),
                    targets,
                    epoch=submission.epoch,
                ),
            )
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
        artifacts = () if resolving else submission.artifacts
        proposals = () if resolving else submission.proposals
        if plan.migrates:
            return self._migrate_across(
                chunk, graph, facts, from_node, submission, plan.edge, targets, artifacts, proposals
            )
        assert plan.to_node_id is not None
        # The transition-time consult — after every rejection and before `record_transition`,
        # so a firing intent or follow-latest drift writes no transition row of its own.
        landing = Landing.consult(chunk, plan.edge, targets)
        if landing is not None:
            return self._land_migration(chunk, graph, from_node, submission, landing, submission.artifacts, proposals)
        return self._transition(chunk, graph, from_node, submission, plan, artifacts, proposals)

    def _transition(
        self,
        chunk: Chunk,
        graph: Graph,
        from_node: Node,
        submission: Completion,
        plan: CompletionPlan,
        artifacts: Sequence[CompletionArtifact],
        proposals: Sequence[ItemProposal],
    ) -> ApplyResult:
        assert plan.to_node_id is not None
        fresh_transition_id = Id.mint(IdPrefix.TRANSITION, self._clock).value
        artifact_rows = self._artifact_rows(chunk, from_node, submission.epoch, artifacts)
        proposal_rows = self._proposal_rows(chunk, from_node, submission, proposals)
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            locked = handle.facts(chunk.chunk_id)
            if locked is None:
                return ApplyResult.failure(f"unknown chunk {chunk.chunk_id}")
            # A racing duplicate at this (node, epoch) answers as the replay it is, never as a
            # refusal for no longer standing at `from_node`.
            raced = self._landed_under_lock(locked, submission)
            if raced is None:
                try:
                    refuse_incoherent_attempt(locked, graph, from_node=from_node, epoch=submission.epoch)
                except CompletionRefused as refused:
                    return ApplyResult.failure(refused.detail)
                refusal = self._movement.record_transition_locked(
                    handle,
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
                    artifacts=artifact_rows,
                    proposals=proposal_rows,
                    decision_id=submission.decision_id,
                )
                if refusal is not None:
                    return ApplyResult.failure(refusal.detail)
        if raced is not None:
            return self._answer_raced(chunk, graph, from_node, submission, raced)
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
        graph: Graph,
        facts: ChunkFacts,
        from_node: Node,
        submission: Completion,
        edge: Edge,
        targets: MigrationTargets,
        artifacts: Sequence[CompletionArtifact],
        proposals: Sequence[ItemProposal],
    ) -> ApplyResult:
        """Take a cross-graph edge — land on its resolved target, or escalate once per epoch. An
        unresolved target answers ``PARKED_AT_GATE``: ``FAILURE`` would requeue and supersede it."""
        if re_escalates(facts, edge, targets, epoch=submission.epoch):
            return ApplyResult.escalated(edge.target_graph)
        if targets.cross_graph is not None:
            landing = Landing.authored(edge, targets.cross_graph, from_node)
            return self._land_migration(chunk, graph, from_node, submission, landing, artifacts, proposals)
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
        graph: Graph,
        from_node: Node,
        submission: Completion,
        landing: Landing,
        artifacts: Sequence[CompletionArtifact],
        proposals: Sequence[ItemProposal],
    ) -> ApplyResult:
        """Record the migration atomically (fact + re-pin + artifacts + proposals + route
        release/retain + intent clear), then govern by the landed node's executor."""
        artifact_rows = self._artifact_rows(chunk, from_node, submission.epoch, artifacts)
        proposal_rows = self._proposal_rows(chunk, from_node, submission, proposals)
        migration_id = Id.mint(IdPrefix.MIGRATION, self._clock).value
        with self._exclusive.locked([chunk.chunk_id]) as handle:
            locked = handle.facts(chunk.chunk_id)
            if locked is None:
                return ApplyResult.failure(f"unknown chunk {chunk.chunk_id}")
            raced = self._landed_under_lock(locked, submission)
            recorded: str | None = None
            if raced is None:
                try:
                    refuse_incoherent_attempt(locked, graph, from_node=from_node, epoch=submission.epoch)
                except CompletionRefused as refused:
                    return ApplyResult.failure(refused.detail)
                outcome = self._movement.record_migration_locked(
                    handle,
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
                    artifacts=artifact_rows,
                    proposals=proposal_rows,
                    release_route=landing.releases_route,
                    clear_intent=landing.clear_intent,
                    migration_id=migration_id,
                )
                if isinstance(outcome, FenceRefusal):
                    return ApplyResult.failure(outcome.detail)
                recorded = outcome
        if raced is not None:
            return self._answer_raced(chunk, graph, from_node, submission, raced)
        _CP_MIGRATE_AFTER_RECORD.reached()
        landed_node = landing.node
        if not landing.releases_route:
            assert landed_node is not None
            self._hub_node_executor.run(chunk, landing.graph, landed_node, epoch=submission.epoch)
            return ApplyResult.landed_on_hub(landed_node, recorded)
        return ApplyResult.migrated(from_node, landing.graph, recorded)

    @staticmethod
    def _landed_under_lock(locked: ChunkFacts, submission: Completion) -> _Raced | None:
        """The movement a concurrent submission already recorded at this submission's
        ``(node, epoch)``, read from the facts loaded under the row lock."""
        target = locked.accepted_transition_target(from_node_id=submission.from_node_id, epoch=submission.epoch)
        if target is not None:
            return _Raced(transition_to=target)
        if any(m.from_node_id == submission.from_node_id and m.epoch == submission.epoch for m in locked.migrations):
            return _Raced(
                migration=replayed_migration(locked, from_node_id=submission.from_node_id, epoch=submission.epoch)
            )
        return None

    def _answer_raced(
        self, chunk: Chunk, graph: Graph, from_node: Node, submission: Completion, raced: _Raced
    ) -> ApplyResult:
        if raced.migration is not None:
            return ApplyResult.replayed(raced.migration, epoch=submission.epoch)
        assert raced.transition_to is not None
        return self._respond(chunk, graph, from_node, submission, to_node_id=raced.transition_to, is_fresh_apply=False)

    def _respond(
        self,
        chunk: Chunk,
        graph: Graph,
        from_node: Node,
        submission: Completion,
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
            # Run on BOTH the fresh apply and the replay: a re-flush resumes an interrupted run
            # (a crashed one's slot is released at boot), while the executor defers a replay whose
            # run is live or already left the node, so it never starts a second one.
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
        return ApplyResult.advance(envelope, transition_id)

    def _open_graph_gate_decision(self, chunk: Chunk, gate_node: Node, *, epoch: int, claimant: Claimant) -> None:
        """Open the graph gate's decision on arrival — idempotent per (chunk, node, epoch) by the
        natural-key probe. No artifacts attach: they arrived with the transition into the gate."""
        if self._decisions.find_decision(chunk.chunk_id, node_id=gate_node.node_id, epoch=epoch) is not None:
            return
        # A refusal — the chunk was stopped or restarted since the arrival was recorded —
        # leaves the decision unopened: it would gate a superseded visit.
        self._decisions.record_decision(
            decision_id=Id.mint(IdPrefix.DECISION, self._clock).value,
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
        self, chunk: Chunk, facts: ChunkFacts, submission: Completion, *, route_token_mode: str
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
        self, chunk: Chunk, node: Node, epoch: int, artifacts: Sequence[CompletionArtifact]
    ) -> list[StoredArtifact]:
        ids = [Id.mint(IdPrefix.ARTIFACT, self._clock).value for _ in artifacts]
        return stored_artifacts(chunk.chunk_id, node, epoch, artifacts, artifact_ids=ids)

    def _proposal_rows(
        self, chunk: Chunk, node: Node, submission: Completion, proposals: Sequence[ItemProposal]
    ) -> list[StampedWorkItemProposal]:
        ids = [Id.mint(IdPrefix.WORK_ITEM_PROPOSAL, self._clock).value for _ in proposals]
        return stamped_proposals(
            chunk.chunk_id, node, submission.epoch, proposals, proposal_ids=ids, runner_id=submission.runner_id
        )
