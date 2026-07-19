"""Completion apply — the advancement checkpoint.

``POST /chunks/{id}/completions`` submits one node-step's completion; this rule
applies it. The write is **atomic** (the transition and its artifacts land together),
**epoch-fenced** (a submission whose epoch is not the chunk's latest is
rejected before anything is written — a zombie's work never lands), and
**idempotent** (a re-applied completion — the lost-response replay — returns
the same outcome without a second transition).

The apply-response is what lets the runner continue in place: a runner node
returns the next envelope; a hub node is taken over by the generic
:class:`~blizzard.hub.delivery.hub_node.HubNodeExecutor` and returns
``hub_node_taken``; the reserved terminal returns ``done``; a human gate
parks the chunk on an open **Decision** (``parked_at_gate``). Ordering matters
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
from blizzard.foundation.crash import crashpoint
from blizzard.foundation.ids import ARTIFACT_PREFIX, DECISION_PREFIX, TRANSITION_PREFIX, mint
from blizzard.hub.config import ROUTE_TOKEN_WARN
from blizzard.hub.delivery.hub_node import HubNodeExecutor
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.envelope import build_node_envelope
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Edge, Executor, Graph, JudgedBy, Node
from blizzard.hub.domain.route_auth import check_route_token
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    ChunkStatus,
    DecisionChoice,
    IWriteChunkRepository,
    derive_chunk_status,
    landing_node,
    latest_epoch,
)
from blizzard.wire.completion import CompletionSubmission, SubmittedArtifact
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse

_TERMINAL_STATUSES = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})

# The cross-graph migration crash window (issue #90, ``bzh:crash-point-registry``): the
# migration fact, the graph/model re-pin, the route release, and the node-step's
# artifacts are already committed in one transaction — a ``kill -9`` here loses only the
# ``MIGRATED`` response, so the runner's replayed completion re-derives it via the
# ``accepted_migration`` probe (idempotent), and the invariant checker's
# ``hub:migration-pin-consistent`` holds because the re-pin landed atomically with the fact.
_CP_MIGRATE_AFTER_RECORD = crashpoint(
    "migrate.after-record.before-response",
    "migration recorded (graph/model re-pinned, route released, artifacts committed); MIGRATED response not returned",
)


def _failure(detail: str) -> ApplyResponse:
    return ApplyResponse(outcome=ApplyOutcome.FAILURE, detail=detail)


def _migrated(from_node: Node, target_graph: Graph) -> ApplyResponse:
    """The fresh ``MIGRATED`` apply-response (issue #90) — the chunk re-pinned + re-queued;
    the runner tears the attempt down rather than continuing in place."""
    return ApplyResponse(
        outcome=ApplyOutcome.MIGRATED,
        detail=f"node `{from_node.name}` migrated the chunk to graph `{target_graph.name}`; re-queued",
    )


def _migrated_replay() -> ApplyResponse:
    """The replayed ``MIGRATED`` apply-response (issue #90) — a lost-ack re-flush of a
    completion whose migration already landed. Carries no node/graph detail: the migration
    re-pinned the graph, so the submitting node no longer lives in the chunk's current pin,
    and the natural-key probe alone (not a graph lookup) resolves the replay."""
    return ApplyResponse(outcome=ApplyOutcome.MIGRATED, detail="chunk already migrated (replay)")


class ApplyService:
    """Apply a node-step completion to a chunk, fenced and idempotent."""

    def __init__(
        self,
        *,
        chunks: IWriteChunkRepository,
        clock: IClock,
        hub_node_executor: HubNodeExecutor,
    ) -> None:
        self._chunks = chunks
        self._clock = clock
        self._hub_node_executor = hub_node_executor

    def apply(
        self,
        chunk: Chunk,
        graph: Graph,
        submission: CompletionSubmission,
        *,
        route_token_mode: str = ROUTE_TOKEN_WARN,
        target_graph: Graph | None = None,
    ) -> ApplyResponse:
        """Apply a completion. ``target_graph`` is the pre-resolved cross-graph migration
        target (issue #90) — the edge caller resolves the chosen edge's ``graph:<name>``
        via the read graph repository and passes the ``Graph`` (or ``None`` if it names no
        enabled graph) here, so this stays a pure taker-of-objects (``bzh:domain-takes-objects``)
        holding no graph repo of its own."""
        facts = self._chunks.load_facts(chunk.chunk_id)
        if facts is None:
            return _failure(f"unknown chunk {chunk.chunk_id}")

        # Route-token authorization (issue #84b) — ordered ahead of everything else
        # below, including the idempotent-replay probe: a replay is a write-path
        # short-circuit too, and a post-release zombie's replayed completion must be
        # rejected exactly as a fresh one would be (the plan's "release invalidates the
        # token" requirement). The existing epoch fence (further down, and in
        # ``_apply_gate_resolution``) runs after this and is untouched.
        rejection = self._check_route_token(chunk, facts, submission, route_token_mode=route_token_mode)
        if rejection is not None:
            return rejection

        # A migration writes no transition and **re-pins the graph** (issue #90), so on a
        # replay the submission's ``from_node_id`` no longer lives in the chunk's now-current
        # pinned graph — probe it by the natural key *before* the graph-node lookup below,
        # else a legitimate lost-ack replay 404s its own from-node in the new graph. Ordered
        # after the route-token check (a post-release zombie is still rejected first) but
        # ahead of everything graph-shaped.
        if self._chunks.accepted_migration(
            chunk.chunk_id, from_node_id=submission.from_node_id, epoch=submission.epoch
        ):
            return _migrated_replay()

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
            return self._respond(chunk, graph, from_node, submission, to_node_id=replayed, is_fresh_apply=False)

        # A completion carrying a decision id is a gate-resolving transition —
        # graph gate (human node) or runner-config gate (worker node): validate and
        # record it against the resolved decision, marking that decision transitioned.
        if submission.decision_id is not None:
            return self._apply_gate_resolution(chunk, graph, from_node, submission, target_graph)
        # A plain transition OUT of a human-judged node is rejected — human signoff
        # required; only the resolving transition above may leave a gate node.
        if from_node.judged_by is JudgedBy.HUMAN:
            return _failure(f"human signoff required: node `{from_node.name}` is a gate — resolve its decision")

        if derive_chunk_status(facts) in _TERMINAL_STATUSES:
            return _failure("chunk is terminal")
        latest = latest_epoch(facts)
        if latest is not None and submission.epoch != latest:
            return _failure(f"stale epoch {submission.epoch}; chunk is at {latest}")

        edge = graph.edge_for_choice(from_node.node_id, submission.choice)
        if edge is None:
            return _failure(f"node {from_node.name} has no choice `{submission.choice}`")
        # A cross-graph edge (issue #90) migrates the chunk rather than transitioning it.
        if edge.target_graph is not None:
            return self._apply_migration(chunk, from_node, submission, edge, target_graph)
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
        return self._respond(chunk, graph, from_node, submission, to_node_id=to_node_id, is_fresh_apply=True, edge=edge)

    def _apply_gate_resolution(
        self,
        chunk: Chunk,
        graph: Graph,
        gate_node: Node,
        submission: CompletionSubmission,
        target_graph: Graph | None = None,
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
        # A human gate's resolved choice may itself target another graph (issue #90) —
        # the migration branch is reached through here too (the gate's decision artifacts
        # already landed, so the migration carries none of its own). It threads
        # ``submission.decision_id`` through, so the resolved decision derives closed —
        # a migration writes no transitions row, and an unclosed gate decision would wedge
        # REAP recovery (``steps.py`` skips any chunk whose ``decision`` is non-None).
        if edge.target_graph is not None:
            return self._apply_migration(chunk, gate_node, submission, edge, target_graph, artifacts=[])
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
        return self._respond(chunk, graph, gate_node, submission, to_node_id=to_node_id, is_fresh_apply=True, edge=edge)

    def _apply_migration(
        self,
        chunk: Chunk,
        from_node: Node,
        submission: CompletionSubmission,
        edge: Edge,
        target_graph: Graph | None,
        *,
        artifacts: list[SubmittedArtifact] | None = None,
    ) -> ApplyResponse:
        """Take a cross-graph migration edge (issue #90) — re-pin + re-queue, or escalate.

        When the caller resolved the target (``target_graph`` set): record the migration
        (which re-pins the graph/model, releases the route, and commits this node-step's
        artifacts atomically), landing on the name-match-else-entry node of the target
        graph, and return ``MIGRATED``. When this migration is a **human gate's** resolved
        choice (``submission.decision_id`` set — reached via ``_apply_gate_resolution``),
        the migration fact carries that ``decision_id`` so the decision derives closed;
        without it the gate's decision would stay a phantom live decision (mis-rendered on
        the board, and — worse — blocking REAP from ever reclaiming the chunk). When the caller could **not** resolve it
        (``target_graph is None`` — the ``graph:<name>`` names no enabled graph):
        ``record_escalation`` so the chunk derives ``needs_human`` (visible on the board),
        rather than crash or silently drop — and return ``PARKED_AT_GATE`` so the runner
        stops without re-leasing (a ``FAILURE`` would requeue and *supersede* the very
        escalation just recorded). Idempotent on replay by the migration natural key
        (checked in ``apply``) and, on the escalation branch, by an existing escalation at
        this epoch."""
        if target_graph is None:
            facts = self._chunks.load_facts(chunk.chunk_id)
            already = facts is not None and any(e.epoch == submission.epoch for e in facts.escalations)
            if not already:
                self._chunks.record_escalation(
                    chunk.chunk_id,
                    epoch=submission.epoch,
                    takeover_command=(
                        f"cross-graph target `{edge.target_graph}` names no enabled graph — mint a graph "
                        f"named `{edge.target_graph}` (or edit the choice), then requeue this chunk"
                    ),
                    at=self._clock.now(),
                )
            return ApplyResponse(
                outcome=ApplyOutcome.PARKED_AT_GATE,
                detail=f"cross-graph target `{edge.target_graph}` did not resolve; chunk escalated for a human",
            )
        submitted = submission.artifacts if artifacts is None else artifacts
        self._chunks.record_migration(
            chunk.chunk_id,
            from_node_id=from_node.node_id,
            from_graph_id=from_node.graph_id,
            to_graph_id=target_graph.graph_id,
            landed_node_id=landing_node(target_graph, from_node.name),
            choice_name=submission.choice,
            decision_id=submission.decision_id,
            model=edge.model,
            epoch=submission.epoch,
            at=self._clock.now(),
            artifacts=[self._row(chunk, from_node, submission.epoch, a) for a in submitted],
        )
        _CP_MIGRATE_AFTER_RECORD.reached()
        return _migrated(from_node, target_graph)

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
    ) -> ApplyResponse:
        if to_node_id == RESERVED_TERMINAL:
            return ApplyResponse(outcome=ApplyOutcome.DONE, detail="chunk reached the terminal")
        to_node = graph.node_by_id(to_node_id)
        if to_node is None:
            return _failure(f"transition target {to_node_id} is not a node")

        if to_node.executor is Executor.HUB:
            # Every hub node (#67 — no engine-privileged node name remains) is driven
            # by the generic HubNodeExecutor. Run on BOTH the fresh apply and the
            # idempotent replay (``is_fresh_apply`` is ignored here): the executor is
            # itself idempotent and resumable, so a completion re-flushed after a
            # mid-run hub crash RESUMES the interrupted run rather than wedging the
            # chunk at ``delivering``.
            self._hub_node_executor.run(chunk, graph, to_node, epoch=submission.epoch)
            return ApplyResponse(
                outcome=ApplyOutcome.HUB_NODE_TAKEN,
                detail=f"hub node `{to_node.name}` took over; poll the chunk for the outcome",
            )
        if to_node.judged_by is JudgedBy.HUMAN:
            # A transition INTO a human-judged node opens a graph gate: park on a decision
            # carrying the node's choice set. Only on the real apply, never a replay.
            if is_fresh_apply:
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
        never reaches here (is_fresh_apply=False), and the natural-key probe guards a
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

    def _check_route_token(
        self, chunk: Chunk, facts: ChunkFacts, submission: CompletionSubmission, *, route_token_mode: str
    ) -> ApplyResponse | None:
        route = self._chunks.route_of(chunk.chunk_id)
        detail = check_route_token(
            facts,
            presented_token=submission.route_token,
            submission_runner_id=submission.runner_id,
            route_runner_id=route.runner_id if route is not None else None,
            mode=route_token_mode,
        )
        return _failure(detail) if detail is not None else None

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
