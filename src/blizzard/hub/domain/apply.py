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
from blizzard.hub.config import PRODUCES_WARN, ROUTE_TOKEN_WARN
from blizzard.hub.delivery.hub_node import HubNodeExecutor
from blizzard.hub.domain.artifacts import ArtifactKind, ArtifactRow
from blizzard.hub.domain.envelope import build_node_envelope
from blizzard.hub.domain.graph import RESERVED_TERMINAL, Edge, Executor, Graph, JudgedBy, Node
from blizzard.hub.domain.produces_auth import check_produces
from blizzard.hub.domain.route_auth import check_route_token
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    ChunkStatus,
    DecisionChoice,
    IWriteChunkRepository,
    MigrationMode,
    derive_chunk_status,
    landing_node,
    latest_epoch,
)
from blizzard.wire.completion import CompletionSubmission, SubmittedArtifact
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse

_TERMINAL_STATUSES = frozenset({ChunkStatus.STOPPED, ChunkStatus.DONE})

# The cross-graph migration crash window (issue #90, ``bzh:crash-point-registry``): the
# migration fact, the graph/model re-pin, the route release (unless the migration lands on
# a hub node — issue #111 — in which case the route is retained instead), and the
# node-step's artifacts are already committed in one transaction — a ``kill -9`` here loses
# only the ``MIGRATED``/``HUB_NODE_TAKEN`` response, so the runner's replayed completion
# re-derives it via the ``accepted_migration`` probe (idempotent). On a hub landing the
# inline ``HubNodeExecutor.run`` never ran (the crash preceded it) and the replay probe
# short-circuits *above* that re-dispatch, so recovery does not come from re-running it here;
# it comes from the RETAINED route — the replay returns ``HUB_NODE_TAKEN`` so the holding
# runner keeps its environments and its ADVANCE poll drives the landed hub node to its
# outcome (``bzh:crash-point-registry``). The invariant checker's
# ``hub:migration-pin-consistent`` holds because the re-pin landed atomically with the fact,
# and ``hub:migration-route-released`` exempts the hub landing (the retained route is intended).
_CP_MIGRATE_AFTER_RECORD = crashpoint(
    "migrate.after-record.before-response",
    "migration recorded (graph/model re-pinned, route released unless hub-landing, artifacts committed);"
    " response not yet returned",
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
    **runner-landing** migration that already landed. Carries no node/graph detail: the
    migration re-pinned the graph, so the submitting node no longer lives in the chunk's
    current pin, and the natural-key probe alone (not a graph lookup) resolves the replay."""
    return ApplyResponse(outcome=ApplyOutcome.MIGRATED, detail="chunk already migrated (replay)")


def _hub_node_taken_replay() -> ApplyResponse:
    """The replayed ``HUB_NODE_TAKEN`` apply-response (issue #111) — a lost-ack re-flush of a
    completion whose migration landed on a **hub-executed** node. Like :func:`_migrated_replay`
    it carries no node/graph detail (the natural-key probe alone resolves the replay), but its
    outcome is ``HUB_NODE_TAKEN``, not ``MIGRATED``: a hub landing **retained** the route, so
    the holding runner must KEEP its environments and drive the landed hub node via its ADVANCE
    poll. A ``MIGRATED`` reply here would make the runner release the route (``_apply_response``)
    and strand the chunk at ``delivering`` — the inline ``HubNodeExecutor.run`` never ran (the
    crash preceded it) and this replay short-circuits above it, so the ADVANCE poll is the only
    thing left to carry the landed hub node to its outcome."""
    return ApplyResponse(outcome=ApplyOutcome.HUB_NODE_TAKEN, detail="chunk migrated onto a hub node (replay)")


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
        produces_mode: str = PRODUCES_WARN,
        target_graph: Graph | None = None,
        intended_target_graph: Graph | None = None,
    ) -> ApplyResponse:
        """Apply a completion. ``target_graph`` is the pre-resolved cross-graph migration
        target (issue #90) — the edge caller resolves the chosen edge's ``graph:<name>``
        via the read graph repository and passes the ``Graph`` (or ``None`` if it names no
        enabled graph) here, so this stays a pure taker-of-objects (``bzh:domain-takes-objects``)
        holding no graph repo of its own. ``intended_target_graph`` is the chunk's own
        standing migration intent's target (issue #124), pre-resolved the same way — the
        controller resolves ``chunk.intended_migration.graph_id`` via the graph repository
        and passes the ``Graph`` (or ``None`` when it is unresolvable/retired) here. It is
        only ever *consulted*, never applied eagerly: at the first fresh transition this
        completion produces, it either fires (recording a migration, never this
        transition) or — for ``auto`` with no destination-name match — falls through to
        an ordinary transition, leaving the intent set for next time."""
        facts = self._chunks.load_facts(chunk.chunk_id)
        if facts is None:
            return _failure(f"unknown chunk {chunk.chunk_id}")

        # A migration writes no transition and **re-pins the graph** (issue #90), so on a
        # replay the submission's ``from_node_id`` no longer lives in the chunk's now-current
        # pinned graph — probe it by the natural key *before* the graph-node lookup below,
        # else a legitimate lost-ack replay 404s its own from-node in the new graph. Ordered
        # **ahead of** the route-token check below (issue #108): ``record_migration``
        # itself releases the route as part of landing, so a lost-ack replay of an already
        # -accepted migration presents a token whose route the migration's own completion
        # released — that is not a zombie signal, and must short-circuit here before the
        # token check would otherwise reject it.
        if self._chunks.accepted_migration(
            chunk.chunk_id, from_node_id=submission.from_node_id, epoch=submission.epoch
        ):
            # A **hub-landing** migration (issue #111) retained the route and derives
            # ``delivering``; its replay must return ``HUB_NODE_TAKEN`` so the holding runner
            # keeps its environments and drives the landed hub node — a ``MIGRATED`` reply
            # would make it release the route and strand the chunk (see ``_hub_node_taken_replay``).
            # The migration fact carries the landed executor, resolved at read time from the
            # target graph; a runner landing keeps the ``MIGRATED`` re-queue behavior.
            replayed = next(
                (
                    m
                    for m in facts.migrations
                    if m.from_node_id == submission.from_node_id and m.epoch == submission.epoch
                ),
                None,
            )
            if replayed is not None and replayed.landed_node_executor is Executor.HUB:
                return _hub_node_taken_replay()
            return _migrated_replay()

        # Route-token authorization (issue #84b) — ordered ahead of everything else
        # below, including the ordinary idempotent-replay probe (``accepted_transition_target``):
        # a replay is a write-path short-circuit too, and a post-release zombie's replayed
        # completion must be rejected exactly as a fresh one would be (the plan's "release
        # invalidates the token" requirement) — for any submission that isn't already carved
        # out above as an accepted migration's own replay (issue #108). A fresh, non-matching
        # submission over a released route is still rejected here first. The existing epoch
        # fence (further down, and in ``_apply_gate_resolution``) runs after this and is
        # untouched.
        rejection = self._check_route_token(chunk, facts, submission, route_token_mode=route_token_mode)
        if rejection is not None:
            return rejection

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
            return self._apply_gate_resolution(chunk, graph, from_node, submission, target_graph, intended_target_graph)
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

        # Produces-artifact backstop (issue #113 phase 5) — ordered here, after every
        # other rejection ahead of this fresh transition, so the check only runs on a
        # submission that is genuinely about to be recorded: never on a replay (probed
        # above), never on a gate resolution (no produces of its own — the decision's
        # artifacts already landed), and never once the chunk is already terminal or
        # stale. Under ``enforce`` a rejection here leaves the fence and the transition
        # untouched, exactly like ``_check_route_token``'s failure above.
        produces_rejection = check_produces(from_node, submission.artifacts, mode=produces_mode)
        if produces_rejection is not None:
            return _failure(produces_rejection)

        # The transition-time consult (issue #124) — the chunk's own standing migration
        # intent, if any, gets its one shot at THIS fresh transition: it either fires
        # (recording a migration in place of the transition below and clearing the
        # intent) or falls through, leaving the intent untouched for the transition
        # after. Ordered after every rejection above (never on a replay, a stale/terminal
        # chunk, or a produces-backstop refusal) and before ``record_transition`` so a
        # firing intent writes no transition row of its own.
        migrated = self._consult_intended_migration(chunk, from_node, submission, edge, intended_target_graph)
        if migrated is not None:
            return migrated

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
        intended_target_graph: Graph | None = None,
    ) -> ApplyResponse:
        """Advance a chunk past a resolved gate — the resolving transition.

        The runner picks the resolution up on PULL and submits this to record the
        transition along the chosen edge, referencing the decision (which marks it
        transitioned). Works for both a graph gate (human node) and a runner-config gate
        (worker node); the decision's artifacts already landed, so this carries none.
        ``intended_target_graph`` (issue #124) is the chunk's own standing migration
        intent's pre-resolved target — see :meth:`apply`."""
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

        # The transition-time consult (issue #124) — see the sibling call in ``apply``.
        # A resolved gate's own migration intent gets its one shot here too, threading
        # ``submission.decision_id`` through so the resolved decision derives closed
        # exactly as the #90 gate-migration branch above does.
        migrated = self._consult_intended_migration(chunk, gate_node, submission, edge, intended_target_graph)
        if migrated is not None:
            return migrated

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
        (which re-pins the graph/model and commits this node-step's artifacts atomically),
        landing on the name-match-else-entry node of the target graph. When this migration
        is a **human gate's** resolved choice (``submission.decision_id`` set — reached via
        ``_apply_gate_resolution``), the migration fact carries that ``decision_id`` so the
        decision derives closed; without it the gate's decision would stay a phantom live
        decision (mis-rendered on the board, and — worse — blocking REAP from ever
        reclaiming the chunk).

        The landed node's executor decides what happens next (issue #111), mirroring
        ``_respond``'s transition-into-a-hub-node branch: when the landed node is
        hub-executed, the route is **retained** (``release_route=False``) rather than
        released, the hub node is run inline via ``HubNodeExecutor.run`` — idempotent and
        resumable, so a re-flush after a mid-run crash resumes rather than wedging the
        chunk — and the response is ``HUB_NODE_TAKEN`` so the holding runner's next ADVANCE
        poll observes the outcome. Otherwise (a runner-landing migration) the route is
        released as before and the response is ``MIGRATED``, re-queuing the chunk for any
        runner to claim.

        When the caller could **not** resolve the target (``target_graph is None`` — the
        ``graph:<name>`` names no enabled graph): ``record_escalation`` so the chunk
        derives ``needs_human`` (visible on the board), rather than crash or silently drop
        — and return ``PARKED_AT_GATE`` so the runner stops without re-leasing (a
        ``FAILURE`` would requeue and *supersede* the very escalation just recorded). When
        this unresolvable migration is a **human gate's** resolved choice
        (``submission.decision_id`` set — reached via ``_apply_gate_resolution``), the
        escalation carries that ``decision_id`` so the decision derives closed (issue
        #110); this branch writes neither a transition nor a migration row, so without it
        the gate's decision stays live forever — wedging REAP recovery and driving a
        per-tick runner re-submit, the exact hazard #90 fixed on the resolvable branch.
        Idempotent on replay by the migration natural key (checked in ``apply``) and, on
        the escalation branch, by an existing escalation at this epoch."""
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
                    decision_id=submission.decision_id,
                )
            return ApplyResponse(
                outcome=ApplyOutcome.PARKED_AT_GATE,
                detail=f"cross-graph target `{edge.target_graph}` did not resolve; chunk escalated for a human",
            )
        submitted = submission.artifacts if artifacts is None else artifacts
        landed_node_id = landing_node(target_graph, from_node.name)
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
            clear_intent=False,
        )

    def _consult_intended_migration(
        self,
        chunk: Chunk,
        from_node: Node,
        submission: CompletionSubmission,
        edge: Edge,
        intended_target_graph: Graph | None,
    ) -> ApplyResponse | None:
        """The transition-time consult (issue #124) — the shared helper wired at both
        common-apply-path transition sites (the ordinary worker verdict in :meth:`apply`
        and the resolved gate in :meth:`_apply_gate_resolution`), each after its own
        destination is resolved and before its own ``record_transition``.

        Returns the migration's :class:`ApplyResponse` when the chunk's standing intent
        fires, or ``None`` to fall through to the caller's ordinary ``record_transition``
        (the transition applies unchanged; the intent, if ``auto`` with no name match,
        stays set for next time). ``intent is None`` (no standing intent) and
        ``intended_target_graph is None`` (the intent's target is unresolvable — never
        minted, or retired since the intent was set) both fall through the same way: a
        retired target is not an error here, just a deferred no-op, so the operator sees
        the still-set intent on ``GET`` and can cancel or re-aim it.

        ``forced`` fires unconditionally, landing on the intent's own named node
        regardless of this transition's destination. ``auto`` fires only when this
        transition's own destination node name also exists on the target graph (a name
        match) — otherwise the transition applies unchanged and the intent stays set for
        the transition after."""
        intent = chunk.intended_migration
        if intent is None or intended_target_graph is None:
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
            clear_intent=True,
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
        clear_intent: bool,
    ) -> ApplyResponse:
        """The landing tail shared by a #90 authored-choice migration
        (:meth:`_apply_migration`) and an issue #124 applied intent
        (:meth:`_consult_intended_migration`) — the only differences between the two
        callers are the landing-node anchor (the departed node's name-match-else-entry
        for #90; the destination/forced node for #124) and ``clear_intent``.

        Records the migration atomically (fact + graph/model re-pin + artifacts +
        route release/retain + intent clear, all in :meth:`record_migration`'s one
        transaction), fires the crash point, then governs by the landed node's executor
        exactly as a transition into that node would (issue #111): a hub-executed
        landing runs inline and retains the route (``HUB_NODE_TAKEN``); a runner-executed
        landing releases the route and re-queues (``MIGRATED``)."""
        landed_node = target_graph.node_by_id(landed_node_id)
        lands_on_hub = landed_node is not None and landed_node.executor is Executor.HUB
        self._chunks.record_migration(
            chunk.chunk_id,
            from_node_id=from_node.node_id,
            from_graph_id=from_node.graph_id,
            to_graph_id=target_graph.graph_id,
            landed_node_id=landed_node_id,
            choice_name=choice_name,
            decision_id=decision_id,
            model=model,
            epoch=submission.epoch,
            at=self._clock.now(),
            artifacts=[self._row(chunk, from_node, submission.epoch, a) for a in artifacts],
            release_route=not lands_on_hub,
            clear_intent=clear_intent,
        )
        _CP_MIGRATE_AFTER_RECORD.reached()
        if lands_on_hub:
            assert landed_node is not None
            self._hub_node_executor.run(chunk, target_graph, landed_node, epoch=submission.epoch)
            return ApplyResponse(
                outcome=ApplyOutcome.HUB_NODE_TAKEN,
                detail=f"migration landed on hub node `{landed_node.name}`; poll the chunk for the outcome",
            )
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
