"""The runner-facing fleet router — every runner->hub call under ``/api/fleet/*`` (issue #87).

Enforcement is structural, not per-route: the router's own ``dependencies`` mean a fleet verb is
authenticated *because of where it is mounted*, and a route declaring its own ``runner_id`` confines
it further through :meth:`FleetRequest.assert_owns` — except the lease-transcript read, whose
:func:`_demand_lease_owner` always raises rather than deferring (D3, issue #249)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api import chunks as chunks_api
from blizzard.hub.api import questions as questions_api
from blizzard.hub.api import queue as queue_api
from blizzard.hub.api import runners as runners_api
from blizzard.hub.api import transcripts as transcripts_api
from blizzard.hub.api.auth import AuthMode, RunnerPrincipal, require_runner_principal
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.ingest_broadcast import IngestBroadcast
from blizzard.hub.composition import HubServices
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.hub_node import PollPolicy
from blizzard.hub.domain.claim import ClaimConflict, ClaimDeniedPaused, ClaimDeniedTerminal
from blizzard.hub.domain.envelope import Arrival, Envelope
from blizzard.hub.domain.graph import FollowLatest, Graph, Mint
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
)
from blizzard.wire.chunk import ChunkDetail, ChunkPauseRequest, ChunkSummary, HubAdvanceResponse, WorkItemsView
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeEnvelope
from blizzard.wire.facts import (
    EscalationReport,
    LeaseMintReport,
    RunnerFactAck,
    RunnerFactBatch,
)
from blizzard.wire.fleet import FleetSummaryView
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekResponse
from blizzard.wire.route import (
    RouteClaim,
    RouteClaimConflict,
    RouteClaimPausedDenial,
    RouteClaimResponse,
    RouteClaimTerminalDenial,
    RouteTokenRekeyResponse,
)
from blizzard.wire.runner import RunnerRegistrationRequest, RunnerRegistrationResponse, RunnerView
from blizzard.wire.transcript_segment import LeaseTranscriptView, TranscriptSegmentAck, TranscriptSegmentBatch

_log = get_logger("blizzard.hub.fleet")

router = APIRouter(prefix="/api/fleet", tags=["fleet"], dependencies=[Depends(require_runner_principal)])


@dataclass(frozen=True)
class FleetRequest:
    """One fleet-router call: who it resolved to, and the hub policy it is judged under.

    Ownership is asked of this object rather than of :attr:`principal`, which is ``None``
    under ``warn`` — absent, not mismatched."""

    principal: RunnerPrincipal | None
    mode: AuthMode
    config: HubConfig

    @classmethod
    def of(
        cls,
        request: Request,
        principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
    ) -> FleetRequest:
        return cls(principal, AuthMode.of(request), request.app.state.config)

    @property
    def route_token_mode(self) -> str:
        return self.config.route_token_mode

    @property
    def produces_mode(self) -> str:
        return self.config.produces_mode

    @property
    def follow_latest(self) -> bool:
        return bool(self.config.follow_latest)

    def assert_owns(self, runner_id: str) -> None:
        """Reject a call whose declared ``runner_id`` differs from the resolved principal's
        — only ever fires once a token *did* resolve, to some other runner (issue #86a)."""
        if self.principal is None or self.principal.runner_id == runner_id:
            return
        self.mode.refuse(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"token belongs to runner {self.principal.runner_id!r}, not the declared {runner_id!r}",
            event="runner_id mismatch",
            declared_runner_id=runner_id,
            token_runner_id=self.principal.runner_id,
        )


def _demand_lease_owner(principal: RunnerPrincipal, owning_runner_id: str | None) -> None:
    """The lease-transcript read route's own ownership gate (D3, issue #249) — **always**
    raises on a mismatch, unlike :meth:`FleetRequest.assert_owns`, which ``runner_auth_mode``
    leaves inert by default. ``owning_runner_id=None`` is Decision 1's "hub holds nothing"
    branch, not a refusal — left for the caller to fall back on."""
    if owning_runner_id is not None and owning_runner_id != principal.runner_id:
        # The owning runner's id stays out of the response — logged server-side instead,
        # where an operator, not another runner, can see it.
        _log.warning(
            "lease-transcript ownership mismatch",
            owning_runner_id=owning_runner_id,
            requesting_runner_id=principal.runner_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="lease segments belong to another runner")


@dataclass(frozen=True)
class MigrationTargets:
    """The three graphs one completion's apply may be moved onto — each resolved at the edge so the apply
    service stays a pure taker-of-objects (``bzh:domain-takes-objects``), and each **total**: an
    unresolvable or retired target folds to ``None``, leaving apply's failure path the authoritative one."""

    services: HubServices
    chunk: Chunk
    graph: Graph
    submission: CompletionSubmission
    follow_latest_default: bool

    @property
    def cross_graph(self) -> Graph | None:
        """What a cross-graph migration edge (issue #90) names, resolved by name — ``None`` when the edge
        is not cross-graph, names no enabled graph, or is missing outright (issue #101). Pinned by
        ``tests/test_migration_apply.py::test_an_unresolvable_cross_graph_target_escalates_to_needs_human``."""
        from_node = self.graph.node_by_id(self.submission.from_node_id)
        if from_node is None:
            return None
        edge = self.graph.edge_for_choice(from_node.node_id, self.submission.choice)
        if edge is None or edge.target_graph is None:
            return None
        return self.services.graphs.get_enabled_by_name(edge.target_graph)

    @property
    def intended(self) -> Graph | None:
        """The chunk's standing migration intent (issue #124), resolved by id — ``None`` when none is set,
        the target was never minted, or it has since been retired, which leaves the intent set (pinned by
        ``tests/test_intended_migration_apply.py::test_forced_target_retired_at_consult_is_skipped``)."""
        intent = self.chunk.intended_migration
        if intent is None:
            return None
        target = self.services.graphs.get(intent.graph_id)
        if target is None or self.services.graphs.is_retired(target.graph_id):
            return None
        return target

    @property
    def follow_latest(self) -> Graph | None:
        """The newer same-name mint a follow-latest chunk drifts to (issue #164) — ``None`` when an explicit
        :attr:`intended` wins outright, when the effective policy resolves ``false`` (the graph's own
        tri-state, else the hub default), or when the name resolves to nothing or to no newer mint."""
        if self.chunk.intended_migration is not None:
            return None
        graphs = self.services.graphs
        policy = FollowLatest.of(graphs.follow_latest(self.graph.graph_id), hub_default=self.follow_latest_default)
        if not policy.enabled:
            return None
        newest = graphs.get_enabled_by_name(self.graph.name)
        if newest is None or not Mint.of(newest).newer_than(Mint.of(self.graph)):
            return None
        return newest


# Fleet-side counterparts — delegate to the shared rendering, never duplicate it.


@router.get("/queue/peek", response_model=QueuePeekResponse)
def peek_queue(services: Annotated[HubServices, Depends(get_services)]) -> QueuePeekResponse:
    """The runner's FILL read — the same ready queue as ``GET /api/queue``."""
    return queue_api.get_queue(services)


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkDetail:
    """The runner's chunk-status poll — the same aggregate as ``GET /api/chunks/{chunk_id}``."""
    return chunks_api.get_chunk(chunk_id, services)


@router.get("/chunks/{chunk_id}/work-items", response_model=WorkItemsView)
def get_work_items(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> WorkItemsView:
    """The chunk's work items, read with a runner's own bearer token — the same view as
    ``GET /api/chunks/{chunk_id}/work-items``."""
    return chunks_api.get_work_items(chunk_id, services)


# The fleet-side half of the issue-#55 alias; rationale: :mod:`blizzard.hub.api.chunks`.
router.add_api_route(
    "/chunks/{chunk_id}/pm-items",
    get_work_items,
    methods=["GET"],
    response_model=WorkItemsView,
    deprecated=True,
    name="fleet_get_pm_items_deprecated_alias",
    summary="Deprecated alias for GET /fleet/chunks/{chunk_id}/work-items",
    description=(
        "Deprecated since issue #55 — use `GET /fleet/chunks/{chunk_id}/work-items`, which "
        "this path aliases onto the identical handler and returns the identical view."
    ),
)


@router.post("/chunks/{chunk_id}/pause", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def pause_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
    """Pause the chunk with a runner's own bearer token (issue #185) — the same transition as the
    operator route, ``by`` defaulting to ``operator``."""
    return chunks_api.pause_chunk(chunk_id, ChunkPauseRequest(), services)


@router.post("/chunks/{chunk_id}/resume", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def resume_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
    """Resume the chunk with a runner's own bearer token (issue #185). Takes no body, so the
    resume is always recorded as ``operator``."""
    return chunks_api.resume_chunk(chunk_id, ChunkPauseRequest(), services)


@router.get("/summary", response_model=FleetSummaryView)
def fleet_summary(services: Annotated[HubServices, Depends(get_services)]) -> FleetSummaryView:
    """The fleet-pulse counts (issue #76), read with a runner's own bearer token. Fleet-router-only:
    this read has no anonymous counterpart."""
    return chunks_api.FleetPulse(services).view()


@router.get("/questions/{question_id}", response_model=QuestionView)
def get_question(question_id: str, services: Annotated[HubServices, Depends(get_services)]) -> QuestionView:
    """The runner's answer poll before it resumes the dormant session."""
    row = services.chunks.get_question(question_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown question {question_id}")
    return questions_api.question_view(row)


# Moved wholesale — no anonymous caller ever reached these.


@router.get("/chunks/{chunk_id}/envelope", response_model=NodeEnvelope)
def get_envelope(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> NodeEnvelope:
    """The chunk's current node envelope, idempotent — the lost-apply re-read."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    node_id = facts.current_node_id() or graph.entry_node_id
    node = graph.node_by_id(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="chunk has no current runner node (terminal)")
    return Envelope(
        chunk=chunk,
        graph=graph,
        node=node,
        artifacts=services.chunks.load_artifacts(chunk_id),
        epoch=facts.latest_epoch() or 0,
        arrival_addendum=Arrival.of_transition(graph, facts.newest_transition()).addendum,
    ).wire


@router.post("/chunks/{chunk_id}/hub-advance", response_model=HubAdvanceResponse)
def hub_advance(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
) -> HubAdvanceResponse:
    """Drive a chunk parked at a generic hub command node one step (#65), running that node's
    hub-side command once under the fleet-wide serialization
    slot. ``ran=False`` is never an error: a different chunk holds the slot, or (#66) the node reported
    ``pending`` and ``poll_interval`` has not elapsed, or the chunk is not parked at a hub command node
    at all — ``detail`` names which. The request declares no ``runner_id`` to confine against."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    node_id = facts.current_node_id()
    node = graph.node_by_id(node_id) if node_id is not None else None
    if node is None or not node.is_hub_command_node:
        derived = facts.status()
        return HubAdvanceResponse(
            chunk_id=chunk_id, status=derived, ran=False, detail="not parked at a hub command node"
        )
    change = chunk_events.ChunkChanged.of(services, chunk_id, prev_status=facts.status().value)
    epoch = facts.latest_epoch() or 0
    result = services.hub_node.run(chunk, graph, node, epoch=epoch)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    derived = facts.status()
    # `key` names the transition this call recorded — absent when the poll deferred or wrote a
    # poll-attempt fact instead, since there is no fresh `transitions` row to key on (issue #213).
    advance_key = f"transitions:{result.transition_id}" if result is not None and result.transition_id else None
    change.publish(cause="hub-advanced", key=advance_key)
    if result is None:
        pending = facts.hub_node_pending()
        next_poll_at = pending.polled_at + PollPolicy.of(node).interval if pending is not None else None
        # A future `next_poll_at` distinguishes "not yet due to poll" (#66) from a genuinely busy slot;
        # a pending node whose interval elapsed but lost the slot race falls through to the busy branch.
        if next_poll_at is not None and next_poll_at > services.clock.now():
            detail = f"pending — next poll at {iso_utc(next_poll_at)}"
        else:
            detail = "hub-execution slot busy — try again"
        return HubAdvanceResponse(chunk_id=chunk_id, status=derived, ran=False, detail=detail)
    return HubAdvanceResponse(
        chunk_id=chunk_id,
        status=derived,
        ran=True,
        outcome_choice=result.outcome_choice,
        to_node_name=result.to_node_name or None,
        detail=result.detail,
    )


@router.post("/routes", response_model=RouteClaimResponse, status_code=status.HTTP_201_CREATED)
def claim_route(
    claim: RouteClaim,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> object:
    """Claim a chunk; 403 if the runner is paused at the hub, 409 if already claimed
    or already terminal ({done, stopped}, issue #118), else the first node envelope."""
    fleet.assert_owns(claim.runner_id)
    chunk = services.chunks.get(claim.chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {claim.chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    change = chunk_events.ChunkChanged.before(services, chunk.chunk_id)
    try:
        result = services.claim.claim(
            chunk,
            graph,
            runner_id=claim.runner_id,
            workspace_id=claim.workspace_id,
            environment_ids=claim.environment_ids,
        )
    except ClaimDeniedPaused as exc:
        denial = RouteClaimPausedDenial(chunk_id=claim.chunk_id, runner_id=exc.runner_id)
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=denial.model_dump())
    except ClaimDeniedTerminal as exc:
        terminal_denial = RouteClaimTerminalDenial(chunk_id=claim.chunk_id, status=exc.status.value)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=terminal_denial.model_dump())
    except ClaimConflict as exc:
        conflict = RouteClaimConflict(chunk_id=claim.chunk_id, held_by_runner_id=exc.held_by_runner_id)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    # Hardcoded literal, not a derivation — a fresh claim always lands the chunk at
    # `running` (see `chunk_events.ChunkChanged.publish`'s docstring).
    change.publish(cause="claimed", status="running", key=f"route_created:{result.route_id}")
    services.events.publish_queue_changed()  # the claim removed the chunk from the ready queue
    return RouteClaimResponse(
        chunk_id=result.route.chunk_id,
        runner_id=result.route.runner_id,
        workspace_id=result.route.workspace_id,
        environment_ids=result.route.environment_ids,
        envelope=result.envelope,
        route_token=result.route_token,
    )


@router.post("/chunks/{chunk_id}/route-token", response_model=RouteTokenRekeyResponse)
def rekey_route_token(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> RouteTokenRekeyResponse:
    """Rotate the chunk's live route capability token (issue #84b) — the lost-plaintext recovery for a
    claim whose response was never read back. Confined to the live route's own runner; this route
    presents no chunk-scoped ``route_token`` of its own, which is exactly what it is minting."""
    route = services.chunks.route_of(chunk_id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"chunk {chunk_id} has no live route")
    fleet.assert_owns(route.runner_id)
    route_token = services.claim.rekey(route)
    return RouteTokenRekeyResponse(chunk_id=chunk_id, route_token=route_token)


@router.post("/chunks/{chunk_id}/completions", response_model=ApplyResponse)
def submit_completion(
    chunk_id: str,
    submission: CompletionSubmission,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> ApplyResponse:
    """Apply a node-step's completion atomically; reply carries the next envelope."""
    fleet.assert_owns(submission.runner_id)
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    targets = MigrationTargets(services, chunk, graph, submission, follow_latest_default=fleet.follow_latest)
    # Must precede apply() below — after apply() this always answers True, silencing the
    # publish_queue_changed() fresh-migration check further down.
    already_migrated = services.chunks.accepted_migration(
        chunk_id, from_node_id=submission.from_node_id, epoch=submission.epoch
    )
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    result = services.apply.apply(
        chunk,
        graph,
        submission,
        route_token_mode=fleet.route_token_mode,
        produces_mode=fleet.produces_mode,
        target_graph=targets.cross_graph,
        intended_target_graph=targets.intended,
        follow_latest_graph=targets.follow_latest,
    )
    response = result.response
    fresh_migration = response.outcome is ApplyOutcome.MIGRATED and not already_migrated
    cause = "migrated" if fresh_migration else "node-completed"
    # `key` names the fact this call wrote, per each cause's own mapped fact table (issue #213):
    # `migration_id` only for a genuine `migrated`, `transition_id` only when a fresh row backs it.
    if fresh_migration and result.migration_id is not None:
        key = f"chunk_migrations:{result.migration_id}"
    elif not fresh_migration and result.transition_id is not None:
        key = f"transitions:{result.transition_id}"
    else:
        key = None
    change.publish(cause=cause, key=key)
    if fresh_migration:
        services.events.publish_queue_changed()  # a fresh migration re-queued the chunk under the target graph
    # A completion landing on a human-judged node opens a graph gate: surface it.
    chunks_api.OpenDecision(services, chunk_id).publish()
    return response


@router.post("/chunks/{chunk_id}/decisions", response_model=ApplyResponse)
def submit_decision(
    chunk_id: str,
    submission: DecisionSubmission,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> ApplyResponse:
    """Runner-config gate: park the chunk on a decision in place of a transition."""
    fleet.assert_owns(submission.runner_id)
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    result = services.decisions.submit(chunk, graph, submission, route_token_mode=fleet.route_token_mode)
    key = f"decisions:{result.decision_id}" if result.decision_id is not None else None
    change.publish(cause="decision-submitted", key=key)
    # The runner-config gate parked the chunk on an open decision: surface it.
    chunks_api.OpenDecision(services, chunk_id).publish()
    return result.response


@router.post("/chunks/{chunk_id}/leases", status_code=status.HTTP_202_ACCEPTED)
def report_lease(
    chunk_id: str,
    report: LeaseMintReport,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> dict[str, str]:
    """Land a runner's ``lease.minted`` — keeps the epoch fence in lockstep."""
    fleet.assert_owns(report.runner_id)
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    services.runner_facts.record_lease_minted(chunk_id, epoch=report.epoch, runner_id=report.runner_id)
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/escalations", status_code=status.HTTP_202_ACCEPTED)
def report_escalation(
    chunk_id: str,
    report: EscalationReport,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> dict[str, str]:
    """Land a runner's ``escalation.recorded`` — the chunk derives ``needs_human``."""
    fleet.assert_owns(report.runner_id)
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    escalation_id = services.runner_facts.record_escalation(
        chunk_id,
        epoch=report.epoch,
        takeover_command=report.takeover_command,
        wrapped_takeover_command=report.wrapped_takeover_command,
    )
    change.publish(cause="escalated", key=f"escalations:{escalation_id}")
    return {"chunk_id": chunk_id}


@router.post("/events", response_model=RunnerFactAck)
def ingest_runner_facts(
    batch: RunnerFactBatch,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> RunnerFactAck:
    """Land runner-minted facts — idempotent on the batch's per-runner ``seq`` high-water mark,
    with each freshly-applied fact re-broadcast on the SSE stream."""
    fleet.assert_owns(batch.runner_id)
    broadcast = IngestBroadcast.before_ingest(services, batch)
    result = services.facts.ingest(batch, route_token_mode=fleet.route_token_mode)
    broadcast.publish(result)
    return result.ack


@router.post("/transcripts", response_model=TranscriptSegmentAck)
def ingest_transcript_segments(
    batch: TranscriptSegmentBatch,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> TranscriptSegmentAck:
    """Land the runner's batched transcript records — the transcript lane's own
    store-and-forward push (D7), distinct from the fact lane at ``POST /api/fleet/events``."""
    fleet.assert_owns(batch.runner_id)
    records = [
        (record.seq, transcripts_api.to_domain_record(record, runner_id=batch.runner_id)) for record in batch.records
    ]
    result = services.transcript_ingest.ingest(batch.runner_id, records)
    return transcripts_api.to_ack(batch.runner_id, result)


@router.get("/chunks/{chunk_id}/transcript-segments", response_model=LeaseTranscriptView)
def get_lease_transcript_segments(
    chunk_id: str,
    node_id: str,
    epoch: int,
    services: Annotated[HubServices, Depends(get_services)],
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> LeaseTranscriptView:
    """A runner's read-back of its own shipped segments (D2/D3, issue #249) — every
    accepted record across every spawn generation under a lease's ``(chunk_id, node_id,
    epoch)``, confined against the ``runner_id`` already on those rows regardless of
    ``runner_auth_mode`` — this route's own always-raising ownership check, not the
    router's mode-gated one."""
    if principal is None:
        # Refused before any store read, not after (an unauthenticated caller must never
        # reach `runner_id_for_lease` at all, default `runner_auth_mode` or not).
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="no resolvable runner token")
    try:
        owner = services.transcripts.runner_id_for_lease(chunk_id, node_id, epoch)
    except RuntimeError as exc:
        # A violated fencing-epoch invariant: an integrity bug elsewhere, never a caller
        # error, and undeclared by the seam — logged with context, then a definite 500.
        _log.error(
            "lease-transcript fencing invariant violated",
            chunk_id=chunk_id,
            node_id=node_id,
            epoch=epoch,
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="lease segments in an inconsistent state"
        ) from exc
    _demand_lease_owner(principal, owner)
    records = services.transcripts.records_for_lease(chunk_id, node_id, epoch, principal.runner_id)
    return transcripts_api.lease_content_view(chunk_id, node_id, epoch, records)


@router.post("/runners", response_model=RunnerRegistrationResponse, status_code=status.HTTP_201_CREATED)
def register_runner(
    request: RunnerRegistrationRequest,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> RunnerRegistrationResponse:
    """Register a runner — runner id + workspace binding; idempotent upsert.

    Runner-auth is checked at the router level (issue #86a); issue #95's optional
    ``url``/``redirect_uris`` extension rides the same authenticated write."""
    fleet.assert_owns(request.runner_id)
    first = services.fleet.register(
        request.runner_id,
        request.workspace_id,
        env_capacity=request.env_capacity,
        public_url=request.url,
        redirect_uris=tuple(request.redirect_uris),
    )
    services.events.publish_runner_changed(request.runner_id, kind="registered")
    return RunnerRegistrationResponse(runner_id=request.runner_id, first_registration=first)


@router.post("/runners/{runner_id}/heartbeats", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat_runner(
    runner_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> Response:
    """Refresh a runner's liveness — the slow runner-level heartbeat. Returns 204."""
    fleet.assert_owns(runner_id)
    if not services.fleet.heartbeat(runner_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    services.events.publish_runner_changed(runner_id, kind="heartbeat")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runners/{runner_id}", response_model=RunnerView)
def get_runner(
    runner_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> RunnerView:
    """One runner's declarative state — the runner's own pull read."""
    fleet.assert_owns(runner_id)
    liveness = services.fleet.get_liveness(runner_id)
    if liveness is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    return runners_api.runner_view(liveness, now=services.clock.now())
