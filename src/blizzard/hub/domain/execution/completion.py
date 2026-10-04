"""Completion rules — what one node-step's completion does, decided on loaded values.

Every rule here is pure: it takes the chunk, its facts, the graphs, and the submission, and
returns a decision — the plan to record, the landing to migrate onto, the next step to answer
with — or raises a :class:`CompletionRefused` carrying the failure detail. The apply and decision
services read the stores and the clock, call these, and hand the decision to their ports."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from blizzard.foundation.artifacts import ArtifactKind
from blizzard.foundation.chunk_migration import MigrationMode
from blizzard.foundation.escalation_causes import EscalationCause
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.node_steps import Executor, JudgedBy
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.artifact.model import StoredArtifact
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    DecisionChoice,
    GateDecision,
    MigrationFact,
)
from blizzard.hub.domain.chunk.proposals import StampedWorkItemProposal
from blizzard.hub.domain.execution.auth.produces import Produces
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL, Edge, FollowLatest, Graph, GraphStanding, Node
from blizzard.wire.completion import ChecksGate, CompletionSubmission, SubmittedArtifact, WorkItemProposal


class CompletionRefused(Exception):
    """A completion (or gate submission) refused before anything is recorded; ``detail`` is the
    failure the runner is answered with."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class CompletionNotAtCurrentNode(CompletionRefused):
    """The current attempt reported from a node the chunk does not stand at."""


class ChunkEscalated(CompletionRefused):
    """The attempt escalated; nothing it reports moves the chunk until something supersedes it."""


class OpenQuestion(CompletionRefused):
    """The attempt asked a question still unanswered; it moves on once a human answers."""


class HubExecutedNode(CompletionRefused):
    """The node is hub-executed: the hub's own executor authors its transitions."""


class GateDecisionOpen(CompletionRefused):
    """A runner-configured gate decision is open at this node-step; only its resolution moves on."""


def refuse_incoherent_attempt(facts: ChunkFacts, graph: Graph, *, from_node: Node, epoch: int) -> None:
    """Refuse a report the attempt at ``epoch`` cannot make — one epoch is one node-step attempt.
    At the current epoch the report must come from the current node (:class:`CompletionNotAtCurrentNode`);
    an attempt whose own escalation is open (:class:`ChunkEscalated`) or whose own question is unanswered
    (:class:`OpenQuestion`) is parked, but the hub's own migration-target escalation parks none. A report
    at another epoch is left to the write fence."""
    if epoch == facts.epoch_floor():
        current = facts.current_node(graph)
        if current is not None and current.node_id != from_node.node_id:
            raise CompletionNotAtCurrentNode(
                f"node `{from_node.name}` is not the chunk's current node `{current.name}` at epoch {epoch}"
            )
    escalation = facts.open_escalation_at(epoch)
    if escalation is not None and escalation.cause != EscalationCause.MIGRATION_TARGET_UNRESOLVABLE:
        raise ChunkEscalated(f"the attempt at epoch {epoch} escalated — requeue the chunk before it moves on")
    asked = facts.open_questions_at(epoch)
    if asked:
        raise OpenQuestion(f"question {asked[0].question_id} is open — answer it before the chunk moves on")


def refuse_hub_executed(from_node: Node) -> None:
    """Refuse a runner's report out of a hub-executed node (:class:`HubExecutedNode`)."""
    if from_node.executor is Executor.HUB:
        raise HubExecutedNode(f"node `{from_node.name}` is hub-executed — the hub authors its transitions")


def gate_refusal(graph: Graph, from_node_id: str) -> Node:
    """The node a runner-config gate parks the chunk at, or the refusal: an unknown node, or one
    with no choices to gate."""
    node = graph.node_by_id(from_node_id)
    if node is None:
        raise CompletionRefused(f"no node {from_node_id} in graph {graph.graph_id}")
    if not node.choices:
        raise CompletionRefused(f"node {node.name} has no choices to gate")
    return node


def decision_choices(node: Node) -> list[DecisionChoice]:
    """A gate decision's choices — the node's own choice set."""
    return [DecisionChoice(name=c.name, description=c.description) for c in node.choices]


@domain_model
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


@domain_model
@dataclass(frozen=True)
class CompletionPlan:
    """The edge a completion takes and where it lands in its own graph; ``to_node_id`` is
    ``None`` exactly when the edge crosses graphs, which migrates the chunk instead."""

    edge: Edge
    to_node_id: str | None

    @property
    def migrates(self) -> bool:
        return self._crosses_graphs()

    def _crosses_graphs(self) -> bool:
        return self.to_node_id is None

    @classmethod
    def plain(
        cls,
        graph: Graph,
        from_node: Node,
        submission: CompletionSubmission,
        *,
        open_gate: GateDecision | None,
        produces_mode: str,
    ) -> CompletionPlan:
        """A completion carrying no decision id. Refusals, in order: leaving a human-judged node
        (only its resolving transition may), a runner-configured gate open at this node-step, an
        unknown choice, then — unless the edge crosses graphs — an unknown destination, the
        ``produces`` backstop, and a red check on a choice that requires green ones."""
        if from_node.judged_by is JudgedBy.HUMAN:
            raise CompletionRefused(f"human signoff required: node `{from_node.name}` is a gate — resolve its decision")
        if open_gate is not None and open_gate.is_open:
            raise GateDecisionOpen(f"decision {open_gate.decision_id} is open at node `{from_node.name}` — resolve it")
        edge = graph.edge_for_choice(from_node.node_id, submission.choice)
        if edge is None:
            raise CompletionRefused(f"node {from_node.name} has no choice `{submission.choice}`")
        if edge.target_graph is not None:
            return cls(edge, None)
        to_node_id = cls._destination(graph, edge, submission.choice)
        produces_rejection = Produces(from_node, submission.artifacts).rejection(mode=produces_mode)
        if produces_rejection is not None:
            raise CompletionRefused(produces_rejection)
        selected = next((c for c in from_node.choices if c.name == submission.choice), None)
        if selected is not None and ChecksGate(selected.requires_checks, submission.check_results).violated:
            raise CompletionRefused(f"choice `{submission.choice}` requires green checks but a check is red")
        return cls(edge, to_node_id)

    @classmethod
    def resolving(
        cls,
        graph: Graph,
        gate_node: Node,
        submission: CompletionSubmission,
        decision: GateDecision | None,
        *,
        chunk: Chunk,
    ) -> CompletionPlan:
        """The resolving transition out of a gate: the named decision must be this gate's,
        resolved, to the submitted choice; then the edge and destination refusals."""
        detail = (
            f"decision {submission.decision_id} does not match node `{gate_node.name}`"
            if decision is None
            else decision.resolving_refusal(
                chunk_id=chunk.chunk_id, node_id=gate_node.node_id, node_name=gate_node.name, choice=submission.choice
            )
        )
        if detail is not None:
            raise CompletionRefused(detail)
        edge = graph.edge_for_choice(gate_node.node_id, submission.choice)
        if edge is None:
            raise CompletionRefused(f"gate `{gate_node.name}` has no choice `{submission.choice}`")
        if edge.target_graph is not None:
            return cls(edge, None)
        return cls(edge, cls._destination(graph, edge, submission.choice))

    @staticmethod
    def _destination(graph: Graph, edge: Edge, choice: str) -> str:
        to_node_id = Destination.of(graph, edge).node_id
        if to_node_id is None:
            raise CompletionRefused(f"choice `{choice}` routes to unknown node {edge.to_node_name}")
        return to_node_id


class ReplayedMigration(StrEnum):
    """How a completion whose migration already landed at its (node, epoch) is answered."""

    #: A restart's own re-pin retained the route: the displaced attempt is fenced like any stale one.
    SUPERSEDED_BY_RESTART = "superseded-by-restart"
    #: The migration landed on a hub-executed node and retained the route.
    HUB_NODE_TAKEN = "hub-node-taken"
    #: A runner-landing migration, which released the route.
    MIGRATED = "migrated"


def replayed_migration(facts: ChunkFacts, *, from_node_id: str, epoch: int) -> ReplayedMigration:
    """Classify the migration already recorded at ``(from_node_id, epoch)``."""
    replayed = next((m for m in facts.migrations if m.from_node_id == from_node_id and m.epoch == epoch), None)
    if replayed is not None and replayed.source is MigrationSource.RESTART:
        return ReplayedMigration.SUPERSEDED_BY_RESTART
    if replayed is not None and replayed.landed_node_executor is Executor.HUB:
        return ReplayedMigration.HUB_NODE_TAKEN
    return ReplayedMigration.MIGRATED


@domain_model
@dataclass(frozen=True)
class UnresolvableTarget:
    """The hub's escalation when a cross-graph edge names no enabled graph."""

    takeover_command: str
    detail: str
    cause: EscalationCause = EscalationCause.MIGRATION_TARGET_UNRESOLVABLE

    @classmethod
    def of(cls, edge: Edge) -> UnresolvableTarget:
        return cls(
            takeover_command=(
                f"cross-graph target `{edge.target_graph}` names no enabled graph — mint a graph "
                f"named `{edge.target_graph}` (or edit the choice), then requeue this chunk"
            ),
            detail=f"cross-graph target graph `{edge.target_graph}` names no enabled graph",
        )


@domain_model
@dataclass(frozen=True)
class MigrationTargets:
    """The graphs one completion may move the chunk onto, from what the edge loaded. Each is total:
    an unresolvable or retired target is ``None``. An explicit intent outranks follow-latest."""

    cross_graph: Graph | None
    intended: Graph | None
    follow_latest: Graph | None

    @staticmethod
    def cross_graph_name(graph: Graph, submission: CompletionSubmission) -> str | None:
        """The graph name the submitted choice's edge crosses into, or ``None`` when it stays."""
        return graph.cross_graph_target_name(submission.from_node_id, submission.choice)

    @classmethod
    def of(
        cls,
        chunk: Chunk,
        graph: Graph,
        *,
        cross_graph: Graph | None,
        intent_graph: Graph | None,
        intent_retired: bool,
        follow_latest: FollowLatest,
        newest_same_name: Graph | None,
    ) -> MigrationTargets:
        """``cross_graph`` is the enabled graph the edge names; ``intent_graph`` the intent's graph
        by id (with ``intent_retired``); ``newest_same_name`` the newest enabled mint of the pinned
        graph's name. A retired or unminted intent target leaves the intent set and moves nothing."""
        intent = chunk.intended_migration
        intended = (
            intent_graph
            if intent is not None
            and intent_graph is not None
            and GraphStanding(intent_graph, retired=intent_retired).targetable()
            else None
        )
        drift = follow_latest.target(graph, newest_same_name) if intent is None else None
        return cls(cross_graph=cross_graph, intended=intended, follow_latest=drift)


@domain_model
@dataclass(frozen=True)
class Landing:
    """Where a migration lands the chunk: the target graph and node, why, whether it clears the
    standing intent, and the model the edge re-pins (``None`` keeps it)."""

    graph: Graph
    node_id: str
    source: MigrationSource
    clear_intent: bool = False
    model: str | None = None

    @property
    def node(self) -> Node | None:
        return self.graph.node_by_id(self.node_id)

    @property
    def releases_route(self) -> bool:
        """A landing on a hub-executed node keeps the route; every other landing gives it back."""
        return self._lands_off_hub()

    def _lands_off_hub(self) -> bool:
        node = self.node
        return node is None or node.executor is not Executor.HUB

    @classmethod
    def authored(cls, edge: Edge, target_graph: Graph, from_node: Node) -> Landing:
        """An authored cross-graph edge: the same-named node on the target, else its entry."""
        return cls(
            graph=target_graph,
            node_id=MigrationFact.landing_node(target_graph, from_node.name),
            source=MigrationSource.AUTHORED_EDGE,
            model=edge.model,
        )

    @classmethod
    def consult(cls, chunk: Chunk, edge: Edge, targets: MigrationTargets) -> Landing | None:
        """The transition-time consult, once a plain transition's destination resolved. With an
        intent: none while its target is unresolved; ``forced`` lands on the intent's node; ``auto``
        lands on the destination's same-named node, else none (the intent stays). With no intent,
        follow-latest lands on the same-named node — except a transition into the reserved
        terminal, which would restart the workflow on the target's entry."""
        intent = chunk.intended_migration
        if intent is None:
            if targets.follow_latest is None or edge.to_node_name == RESERVED_TERMINAL:
                return None
            return cls(
                graph=targets.follow_latest,
                node_id=MigrationFact.landing_node(targets.follow_latest, edge.to_node_name),
                source=MigrationSource.FOLLOW_LATEST,
            )
        target = targets.intended
        if target is None:
            return None
        if intent.mode is MigrationMode.FORCED:
            assert intent.node_name is not None  # request-time validation requires this for `forced`
            landed_name = intent.node_name
        elif target.node_by_name(edge.to_node_name) is not None:
            landed_name = edge.to_node_name
        else:
            return None
        landed = target.node_by_name(landed_name)
        assert landed is not None, f"consult resolved landed node `{landed_name}` on graph {target.graph_id}"
        return cls(graph=target, node_id=landed.node_id, source=MigrationSource.INTENT, clear_intent=True)


class NextStepKind(StrEnum):
    """What a recorded transition hands the runner next."""

    DONE = "done"
    MISSING = "missing"
    HUB_TAKES = "hub-takes"
    GATE = "gate"
    ADVANCE = "advance"


@domain_model
@dataclass(frozen=True)
class NextStep:
    """The step after a transition into ``to_node_id``: the terminal ends the chunk; a
    hub-executed node is taken over by the hub; a human-judged node parks on a gate; any other
    node advances the runner with its envelope."""

    kind: NextStepKind
    node: Node | None = None

    @classmethod
    def of(cls, graph: Graph, to_node_id: str) -> NextStep:
        if to_node_id == RESERVED_TERMINAL:
            return cls(NextStepKind.DONE)
        node = graph.node_by_id(to_node_id)
        if node is None:
            return cls(NextStepKind.MISSING)
        if node.executor is Executor.HUB:
            return cls(NextStepKind.HUB_TAKES, node)
        if node.judged_by is JudgedBy.HUMAN:
            return cls(NextStepKind.GATE, node)
        return cls(NextStepKind.ADVANCE, node)


def stored_artifacts(
    chunk_id: str, node: Node, epoch: int, artifacts: Sequence[SubmittedArtifact], *, artifact_ids: Sequence[str]
) -> list[StoredArtifact]:
    """The artifact rows a submission lands, one minted id each. A ``git_commit`` encodes
    ``branch:hash`` and alone carries its repo and forge — the envelope's projection decodes it."""
    rows: list[StoredArtifact] = []
    for artifact, artifact_id in zip(artifacts, artifact_ids, strict=True):
        is_commit = artifact.kind is ArtifactKind.GIT_COMMIT
        rows.append(
            StoredArtifact(
                kind=artifact.kind,
                name=artifact.name,
                data=f"{artifact.branch_name}:{artifact.commit_hash}" if is_commit else (artifact.content or ""),
                repo=artifact.repo if is_commit else None,
                forge=artifact.forge if is_commit else None,
                artifact_id=artifact_id,
                chunk_id=chunk_id,
                node_id=node.node_id,
                node_name=node.name,
                epoch=epoch,
            )
        )
    return rows


def stamped_proposals(
    chunk_id: str,
    node: Node,
    epoch: int,
    proposals: Sequence[WorkItemProposal],
    *,
    proposal_ids: Sequence[str],
    runner_id: str,
) -> list[StampedWorkItemProposal]:
    """The proposal rows a submission lands, one minted id each, in submitted order."""
    return [
        StampedWorkItemProposal.of(
            p,
            proposal_id=proposal_id,
            chunk_id=chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            ordinal=ordinal,
            runner_id=runner_id,
        )
        for ordinal, (p, proposal_id) in enumerate(zip(proposals, proposal_ids, strict=True))
    ]
