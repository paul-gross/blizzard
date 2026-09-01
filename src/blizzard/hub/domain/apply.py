"""Completion apply — the advancement checkpoint.

One node-step's completion is applied here. The write is **atomic**, **epoch-fenced**,
and **idempotent** — and the idempotency probe runs **before** the terminal check. A
transition **into** a human-judged node opens a decision and parks; leaving one is legal
only as the resolving transition."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.chunk_status import TERMINAL_STATUSES
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
from blizzard.foundation.node_steps import Executor, JudgedBy
from blizzard.hub.config import PRODUCES_WARN, ROUTE_TOKEN_WARN
from blizzard.hub.delivery.hub_node import HubNodeExecutor
from blizzard.hub.domain.artifacts import ArtifactRow
from blizzard.hub.domain.chunks.artifacts import IReadChunkArtifactsRepository
from blizzard.hub.domain.chunks.decisions import IWriteChunkDecisionsRepository
from blizzard.hub.domain.chunks.escalations import IWriteChunkEscalationsRepository
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.movement import IWriteChunkMovementRepository
from blizzard.hub.domain.chunks.route import IReadChunkRouteRepository
from blizzard.hub.domain.envelope import Arrival, Envelope
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Edge, Graph, Node
from blizzard.hub.domain.produces_auth import Produces
from blizzard.hub.domain.proposal_auth import ProposalPolicy
from blizzard.hub.domain.proposals import WorkItemProposalRow
from blizzard.hub.domain.route_auth import RouteToken
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    DecisionChoice,
    MigrationFact,
    MigrationMode,
    MigrationSource,
)
from blizzard.wire.completion import ChecksGate, CompletionSubmission, SubmittedArtifact, WorkItemProposal
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeEnvelope

# The cross-graph migration crash window (issue #90, ``bzh:crash-point-registry``): the whole
# migration is committed but its response is not; the replayed completion re-derives it.
_CP_MIGRATE_AFTER_RECORD = crashpoint(
    "migrate.after-record.before-response",
    "migration recorded (graph/model re-pinned, route released unless hub-landing, artifacts committed);"
    " response not yet returned",
)


@dataclass(frozen=True)
class ApplyResult:
    """:meth:`ApplyService.apply`'s own return — the wire :class:`ApplyResponse` plus the
    identity of the durable fact this call itself just wrote (issue #213). At most one of
    the two is ever set, and only on a genuinely fresh write. Not a wire type."""

    response: ApplyResponse
    transition_id: str | None = None
    migration_id: str | None = None

    @classmethod
    def failure(cls, detail: str) -> ApplyResult:
        return cls(response=ApplyResponse(outcome=ApplyOutcome.FAILURE, detail=detail))

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
        escalation this answers (issue #110)."""
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
        """A lost-ack re-flush of a **runner-landing** migration that already landed (issue #90).
        Carries no node/graph detail: the migration re-pinned the graph, so the natural-key probe
        alone (not a graph lookup) resolves the replay. No fresh fact, so no ``migration_id``."""
        return cls(response=ApplyResponse(outcome=ApplyOutcome.MIGRATED, detail="chunk already migrated (replay)"))

    @classmethod
    def hub_node_taken_replay(cls) -> ApplyResult:
        """A lost-ack re-flush of a completion whose migration landed on a **hub-executed** node
        (issue #111). Distinct from :meth:`migrated_replay` because a hub landing **retained** the
        route, which a ``MIGRATED`` reply would release (pinned by tests/test_migration_apply.py)."""
        return cls(
            response=ApplyResponse(
                outcome=ApplyOutcome.HUB_NODE_TAKEN, detail="chunk migrated onto a hub node (replay)"
            )
        )


@dataclass(frozen=True)
class Destination:
    """Where an edge routes inside its own graph: the reserved terminal, a node id, or ``None``
    for a name no node there carries."""

    node_id: str | None

    @classmethod
    def of(cls, graph: Graph, edge: Edge) -> Destination:
        if edge.to_node_name == RESERVED_TERMINAL:
            return cls(RESERVED_TERMINAL)
        node = graph.node_by_name(edge.to_node_name)
        return cls(node.node_id if node is not None else None)


class ApplyService:
    """Apply a node-step completion to a chunk, fenced and idempotent."""

    def __init__(
        self,
        *,
        facts: IReadChunkFactsRepository,
        movement: IWriteChunkMovementRepository,
        decisions: IWriteChunkDecisionsRepository,
        escalations: IWriteChunkEscalationsRepository,
        route: IReadChunkRouteRepository,
        artifacts: IReadChunkArtifactsRepository,
        clock: IClock,
        hub_node_executor: HubNodeExecutor,
    ) -> None:
        self._facts = facts
        self._movement = movement
        self._decisions = decisions
        self._escalations = escalations
        self._route = route
        self._artifacts = artifacts
        self._clock = clock
        self._hub_node_executor = hub_node_executor

    def apply(
        self,
        chunk: Chunk,
        graph: Graph,
        submission: CompletionSubmission,
        *,
        route_token_mode: str = ROUTE_TOKEN_WARN,
        produces_mode: str = PRODUCES_WARN,
        target_graph: Graph | None = None,
        intended_target_graph: Graph | None = None,
        follow_latest_graph: Graph | None = None,
    ) -> ApplyResult:
        """Apply a completion.

        ``target_graph`` (#90), ``intended_target_graph`` (#124), and ``follow_latest_graph``
        (#164) all arrive pre-resolved — ``None`` meaning "names no enabled graph" — so this
        holds no graph repo of its own (``bzh:domain-takes-objects``)."""
        facts = self._facts.load_facts(chunk.chunk_id)
        if facts is None:
            return ApplyResult.failure(f"unknown chunk {chunk.chunk_id}")

        # Probed by natural key ahead of the graph lookup and the route-token check (issues
        # #90, #108): a migration re-pins the graph and releases the route a replay presents.
        if self._movement.accepted_migration(
            chunk.chunk_id, from_node_id=submission.from_node_id, epoch=submission.epoch
        ):
            # A **hub-landing** migration (issue #111) retained the route, so its replay must
            # return ``HUB_NODE_TAKEN`` rather than ``MIGRATED``.
            replayed = next(
                (
                    m
                    for m in facts.migrations
                    if m.from_node_id == submission.from_node_id and m.epoch == submission.epoch
                ),
                None,
            )
            # A restart's own re-pin (#371) retained the route, so a displaced attempt landing
            # LEVEL on its key is fenced like any stale one — never `MIGRATED`, which releases.
            if replayed is not None and replayed.source is MigrationSource.RESTART:
                return ApplyResult.failure(f"superseded by a restart at epoch {submission.epoch}")
            if replayed is not None and replayed.landed_node_executor is Executor.HUB:
                return ApplyResult.hub_node_taken_replay()
            return ApplyResult.migrated_replay()

        # Route-token authorization (issue #84b) — ordered ahead of the replay probe and the
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

        # Proposed-work-item policy refusal (D6) — unconditional, ordered ahead of every
        # dispatch fork below, so none of them carries a proposal past a node that never declared the policy.
        policy_rejection = ProposalPolicy(from_node, submission.proposals).rejection()
        if policy_rejection is not None:
            return ApplyResult.failure(policy_rejection)

        # A completion carrying a decision id is a gate-resolving transition — a graph gate
        # (human node) or a runner-config gate (worker node).
        if submission.decision_id is not None:
            return self._apply_gate_resolution(
                chunk, graph, from_node, submission, target_graph, intended_target_graph, follow_latest_graph
            )
        # Only the resolving transition above may leave a gate node.
        if from_node.judged_by is JudgedBy.HUMAN:
            return ApplyResult.failure(
                f"human signoff required: node `{from_node.name}` is a gate — resolve its decision"
            )

        if facts.status() in TERMINAL_STATUSES:
            return ApplyResult.failure("chunk is terminal")
        latest = facts.latest_epoch()
        if latest is not None and submission.epoch != latest:
            return ApplyResult.failure(f"stale epoch {submission.epoch}; chunk is at {latest}")

        edge = graph.edge_for_choice(from_node.node_id, submission.choice)
        if edge is None:
            return ApplyResult.failure(f"node {from_node.name} has no choice `{submission.choice}`")
        # A cross-graph edge (issue #90) migrates the chunk rather than transitioning it.
        if edge.target_graph is not None:
            return self._apply_migration(chunk, from_node, submission, edge, target_graph)
        to_node_id = Destination.of(graph, edge).node_id
        if to_node_id is None:
            return ApplyResult.failure(f"choice `{submission.choice}` routes to unknown node {edge.to_node_name}")

        # Produces-artifact backstop (issue #113) — ordered after every other rejection, so
        # it runs only on a submission genuinely about to be recorded.
        produces_rejection = Produces(from_node, submission.artifacts).rejection(mode=produces_mode)
        if produces_rejection is not None:
            return ApplyResult.failure(produces_rejection)

        # Checks gate backstop (issue #114) — the same shared predicate `ChecksGate.violated`
        # both gates run, so the two cannot drift (`test_checks_gate_agreement.py`).
        selected = next((c for c in from_node.choices if c.name == submission.choice), None)
        if selected is not None and ChecksGate(selected.requires_checks, submission.check_results).violated:
            return ApplyResult.failure(f"choice `{submission.choice}` requires green checks but a check is red")

        # The transition-time consult (issue #124) — ordered after every rejection above and
        # before ``record_transition``, so a firing intent writes no transition row of its own.
        migrated = self._consult_intended_migration(
            chunk, from_node, submission, edge, intended_target_graph, follow_latest_graph
        )
        if migrated is not None:
            return migrated

        fresh_transition_id = Id.mint(TRANSITION_PREFIX, self._clock).value
        self._movement.record_transition(
            transition_id=fresh_transition_id,
            chunk_id=chunk.chunk_id,
            from_node_id=from_node.node_id,
            to_node_id=to_node_id,
            choice_name=submission.choice,
            epoch=submission.epoch,
            runner_id=submission.runner_id,
            at=self._clock.now(),
            artifacts=[self._row(chunk, from_node, submission.epoch, a) for a in submission.artifacts],
            proposals=self._proposal_rows(
                chunk, from_node, submission.epoch, submission.proposals, runner_id=submission.runner_id
            ),
        )
        return self._respond(
            chunk,
            graph,
            from_node,
            submission,
            to_node_id=to_node_id,
            is_fresh_apply=True,
            edge=edge,
            transition_id=fresh_transition_id,
        )

    def _apply_gate_resolution(
        self,
        chunk: Chunk,
        graph: Graph,
        gate_node: Node,
        submission: CompletionSubmission,
        target_graph: Graph | None = None,
        intended_target_graph: Graph | None = None,
        follow_latest_graph: Graph | None = None,
    ) -> ApplyResult:
        """Advance a chunk past a resolved gate — the resolving transition. Its artifacts
        and proposals already landed at submission time (D2), so every dispatch fork
        below — the authored cross-graph edge, the migration consult, and the plain
        transition alike — carries none of either."""
        assert submission.decision_id is not None  # the caller dispatches only when set
        decision = self._decisions.get_decision(submission.decision_id)
        if decision is None or decision.chunk_id != chunk.chunk_id or decision.node_id != gate_node.node_id:
            return ApplyResult.failure(f"decision {submission.decision_id} does not match node `{gate_node.name}`")
        if decision.resolved_choice is None:
            return ApplyResult.failure(f"decision {submission.decision_id} is not yet resolved")
        if submission.choice != decision.resolved_choice:
            return ApplyResult.failure(
                f"choice `{submission.choice}` is not the resolved choice `{decision.resolved_choice}`"
            )

        facts = self._facts.load_facts(chunk.chunk_id)
        if facts is None:
            return ApplyResult.failure(f"unknown chunk {chunk.chunk_id}")
        if facts.status() in TERMINAL_STATUSES:
            return ApplyResult.failure("chunk is terminal")
        latest = facts.latest_epoch()
        if latest is not None and submission.epoch != latest:
            return ApplyResult.failure(f"stale epoch {submission.epoch}; chunk is at {latest}")

        edge = graph.edge_for_choice(gate_node.node_id, submission.choice)
        if edge is None:
            return ApplyResult.failure(f"gate `{gate_node.name}` has no choice `{submission.choice}`")
        # A resolved choice may itself target another graph (issue #90) — threading
        # ``decision_id`` through is what keeps the gate's decision from staying live.
        if edge.target_graph is not None:
            return self._apply_migration(chunk, gate_node, submission, edge, target_graph, artifacts=[], proposals=[])
        to_node_id = Destination.of(graph, edge).node_id
        if to_node_id is None:
            return ApplyResult.failure(f"choice `{submission.choice}` routes to unknown node {edge.to_node_name}")

        # The transition-time consult (issue #124) — see the sibling call in ``apply``; the
        # override keeps the decision's already-landed proposals off the migration lane too (D2).
        migrated = self._consult_intended_migration(
            chunk, gate_node, submission, edge, intended_target_graph, follow_latest_graph, proposals=[]
        )
        if migrated is not None:
            return migrated

        fresh_transition_id = Id.mint(TRANSITION_PREFIX, self._clock).value
        self._movement.record_transition(
            transition_id=fresh_transition_id,
            chunk_id=chunk.chunk_id,
            from_node_id=gate_node.node_id,
            to_node_id=to_node_id,
            choice_name=submission.choice,
            epoch=submission.epoch,
            runner_id=submission.runner_id,
            at=self._clock.now(),
            artifacts=[],  # the decision's artifacts already landed
            proposals=[],  # ...and so, for the same reason, are its proposals (D2)
            decision_id=submission.decision_id,
        )
        return self._respond(
            chunk,
            graph,
            gate_node,
            submission,
            to_node_id=to_node_id,
            is_fresh_apply=True,
            edge=edge,
            transition_id=fresh_transition_id,
        )

    def _apply_migration(
        self,
        chunk: Chunk,
        from_node: Node,
        submission: CompletionSubmission,
        edge: Edge,
        target_graph: Graph | None,
        *,
        artifacts: list[SubmittedArtifact] | None = None,
        proposals: list[WorkItemProposal] | None = None,
    ) -> ApplyResult:
        """Take a cross-graph migration edge (issue #90) — re-pin + re-queue, or escalate.

        With ``target_graph`` set it records the migration and lands via
        :meth:`_land_migration`. Unresolved, it escalates to ``needs_human`` and answers
        ``PARKED_AT_GATE`` — ``FAILURE`` would requeue and supersede it (issue #110)."""
        if target_graph is None:
            facts = self._facts.load_facts(chunk.chunk_id)
            already = facts is not None and any(e.epoch == submission.epoch for e in facts.escalations)
            if not already:
                # Hub-authored escalation, no runner runtime dir to compose a wrapped
                # takeover command from — leaves wrapped_takeover_command at its store default.
                self._escalations.record_escalation(
                    chunk.chunk_id,
                    epoch=submission.epoch,
                    takeover_command=(
                        f"cross-graph target `{edge.target_graph}` names no enabled graph — mint a graph "
                        f"named `{edge.target_graph}` (or edit the choice), then requeue this chunk"
                    ),
                    at=self._clock.now(),
                    decision_id=submission.decision_id,
                )
            return ApplyResult.escalated(edge.target_graph)
        submitted = submission.artifacts if artifacts is None else artifacts
        submitted_proposals = submission.proposals if proposals is None else proposals
        landed_node_id = MigrationFact.landing_node(target_graph, from_node.name)
        return self._land_migration(
            chunk,
            from_node,
            submission,
            target_graph=target_graph,
            landed_node_id=landed_node_id,
            choice_name=submission.choice,
            decision_id=submission.decision_id,
            model=edge.model,
            artifacts=submitted,
            proposals=submitted_proposals,
            clear_intent=False,
            source=MigrationSource.AUTHORED_EDGE,
        )

    def _consult_intended_migration(
        self,
        chunk: Chunk,
        from_node: Node,
        submission: CompletionSubmission,
        edge: Edge,
        intended_target_graph: Graph | None,
        follow_latest_graph: Graph | None,
        *,
        proposals: list[WorkItemProposal] | None = None,
    ) -> ApplyResult | None:
        """The transition-time consult (issue #124) — the shared helper both transition sites
        call once their destination resolves, before their own ``record_transition``. ``forced``
        fires unconditionally on the intent's own named node; ``auto`` fires only on a
        destination-name match; anything else falls through. ``proposals`` defaults to the
        submission's own list, overridden to ``[]`` by the gate-resolution caller (D2)."""
        submitted_proposals = submission.proposals if proposals is None else proposals
        intent = chunk.intended_migration
        if intent is None:
            return self._consult_follow_latest(
                chunk, from_node, submission, edge, follow_latest_graph, proposals=submitted_proposals
            )
        if intended_target_graph is None:
            return None
        if intent.mode is MigrationMode.FORCED:
            assert intent.node_name is not None  # request-time validation requires this for `forced`
            landed_node_name = intent.node_name
        elif intended_target_graph.node_by_name(edge.to_node_name) is not None:
            landed_node_name = edge.to_node_name
        else:
            return None  # auto, no name match: unchanged transition, intent stays set
        landed_node = intended_target_graph.node_by_name(landed_node_name)
        assert landed_node is not None, (
            f"consult resolved landed node `{landed_node_name}` on graph {intended_target_graph.graph_id}, "
            "but it does not exist there"
        )
        return self._land_migration(
            chunk,
            from_node,
            submission,
            target_graph=intended_target_graph,
            landed_node_id=landed_node.node_id,
            choice_name=submission.choice,
            decision_id=submission.decision_id,
            model=None,
            artifacts=submission.artifacts,
            proposals=submitted_proposals,
            clear_intent=True,
            source=MigrationSource.INTENT,
        )

    def _consult_follow_latest(
        self,
        chunk: Chunk,
        from_node: Node,
        submission: CompletionSubmission,
        edge: Edge,
        follow_latest_graph: Graph | None,
        *,
        proposals: list[WorkItemProposal] | None = None,
    ) -> ApplyResult | None:
        """The standing follow-latest policy's own consult (issue #164), reached only when
        the chunk carries **no** explicit intent. A transition to the reserved terminal is
        the load-bearing no-op: it names no node, so it would land on the target's
        **entry** and restart the workflow (tests/test_follow_latest_policy.py). ``proposals``
        defaults to the submission's own list, per :meth:`_consult_intended_migration`."""
        if follow_latest_graph is None or edge.to_node_name == RESERVED_TERMINAL:
            return None
        return self._land_migration(
            chunk,
            from_node,
            submission,
            target_graph=follow_latest_graph,
            landed_node_id=MigrationFact.landing_node(follow_latest_graph, edge.to_node_name),
            choice_name=submission.choice,
            decision_id=submission.decision_id,
            model=None,
            artifacts=submission.artifacts,
            proposals=submission.proposals if proposals is None else proposals,
            clear_intent=False,
            source=MigrationSource.FOLLOW_LATEST,
        )

    def _land_migration(
        self,
        chunk: Chunk,
        from_node: Node,
        submission: CompletionSubmission,
        *,
        target_graph: Graph,
        landed_node_id: str,
        choice_name: str | None,
        decision_id: str | None,
        model: str | None,
        artifacts: list[SubmittedArtifact],
        proposals: list[WorkItemProposal],
        clear_intent: bool,
        source: MigrationSource,
    ) -> ApplyResult:
        """The landing tail shared by every migration path. Records the migration atomically
        (fact + re-pin + artifacts + proposals + route release/retain + intent clear), then
        governs by the landed node's executor as a transition into it would (issue #111).
        ``migration_id`` is the fresh fact this call wrote (issue #213)."""
        landed_node = target_graph.node_by_id(landed_node_id)
        lands_on_hub = landed_node is not None and landed_node.executor is Executor.HUB
        migration_id = self._movement.record_migration(
            chunk.chunk_id,
            from_node_id=from_node.node_id,
            from_graph_id=from_node.graph_id,
            to_graph_id=target_graph.graph_id,
            landed_node_id=landed_node_id,
            choice_name=choice_name,
            decision_id=decision_id,
            model=model,
            source=source,
            epoch=submission.epoch,
            at=self._clock.now(),
            artifacts=[self._row(chunk, from_node, submission.epoch, a) for a in artifacts],
            proposals=self._proposal_rows(
                chunk, from_node, submission.epoch, proposals, runner_id=submission.runner_id
            ),
            release_route=not lands_on_hub,
            clear_intent=clear_intent,
            migration_id=Id.mint(MIGRATION_PREFIX, self._clock).value,
        )
        _CP_MIGRATE_AFTER_RECORD.reached()
        if lands_on_hub:
            assert landed_node is not None
            self._hub_node_executor.run(chunk, target_graph, landed_node, epoch=submission.epoch)
            return ApplyResult.landed_on_hub(landed_node, migration_id)
        return ApplyResult.migrated(from_node, target_graph, migration_id)

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
        """``transition_id`` (issue #213) is the caller's own freshly-recorded
        ``transitions.transition_id`` on a fresh apply, or ``None`` on a replay
        (``is_fresh_apply=False``); every branch below carries it straight through."""
        if to_node_id == RESERVED_TERMINAL:
            return ApplyResult.done(transition_id)
        to_node = graph.node_by_id(to_node_id)
        if to_node is None:
            return ApplyResult.failure(f"transition target {to_node_id} is not a node")

        if to_node.executor is Executor.HUB:
            # Run on BOTH the fresh apply and the idempotent replay: the executor is itself
            # idempotent and resumable, so a re-flush RESUMES an interrupted run (#67).
            self._hub_node_executor.run(chunk, graph, to_node, epoch=submission.epoch)
            return ApplyResult.taken_over(to_node, transition_id)
        if to_node.judged_by is JudgedBy.HUMAN:
            # A transition INTO a human-judged node opens a graph gate: park on a decision
            # carrying the node's choice set. Only on the real apply, never a replay.
            if is_fresh_apply:
                self._open_graph_gate_decision(chunk, to_node, epoch=submission.epoch)
            return ApplyResult.parked(to_node, transition_id)

        arrival = Arrival(edge) if edge is not None else Arrival.of_choice(graph, from_node, submission.choice)
        envelope = Envelope(
            chunk=chunk,
            graph=graph,
            node=to_node,
            artifacts=self._artifacts.load_artifacts(chunk.chunk_id),
            epoch=submission.epoch,
            arrival_addendum=arrival.addendum,
        )
        return ApplyResult.advance(envelope.wire, transition_id)

    def _open_graph_gate_decision(self, chunk: Chunk, gate_node: Node, *, epoch: int) -> None:
        """Open the graph gate's decision on arrival — idempotent per (chunk, node, epoch).

        The node's own choices become the decision's; no artifacts are attached (they
        arrived with the transition into the gate). The natural-key probe guards a
        double-open."""
        if self._decisions.find_decision(chunk.chunk_id, node_id=gate_node.node_id, epoch=epoch) is not None:
            return
        self._decisions.record_decision(
            decision_id=Id.mint(DECISION_PREFIX, self._clock).value,
            chunk_id=chunk.chunk_id,
            node_id=gate_node.node_id,
            node_name=gate_node.name,
            epoch=epoch,
            choices=[DecisionChoice(name=c.name, description=c.description) for c in gate_node.choices],
            at=self._clock.now(),
            artifacts=[],
            proposals=[],
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

    def _row(self, chunk: Chunk, from_node: Node, epoch: int, artifact: SubmittedArtifact) -> ArtifactRow:
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
        self, chunk: Chunk, from_node: Node, epoch: int, proposals: list[WorkItemProposal], *, runner_id: str
    ) -> list[WorkItemProposalRow]:
        return [
            WorkItemProposalRow.of(
                p,
                proposal_id=Id.mint(WORK_ITEM_PROPOSAL_PREFIX, self._clock).value,
                chunk_id=chunk.chunk_id,
                node_id=from_node.node_id,
                node_name=from_node.name,
                epoch=epoch,
                ordinal=ordinal,
                runner_id=runner_id,
            )
            for ordinal, p in enumerate(proposals)
        ]
