"""The runner-facing fleet router — every runner->hub call under ``/api/fleet/*`` (issue #87).

Enforcement is structural, not per-route: the router's own ``dependencies`` mean a fleet verb is
authenticated *because of where it is mounted*, and a route declaring its own ``runner_id`` confines
it further through :meth:`FleetRequest.assert_owns`."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api import chunks as chunks_api
from blizzard.hub.api import questions as questions_api
from blizzard.hub.api import queue as queue_api
from blizzard.hub.api import runners as runners_api
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


def _resolve_intended_migration_target(services: HubServices, chunk: Chunk) -> Graph | None:
    """The chunk's standing migration intent's target, resolved by id (issue #124) — ``None`` when no
    intent is set, the target was never minted, or it has since been retired. Resolved at the edge so
    the apply service stays a pure taker-of-objects (``bzh:domain-takes-objects``); a retired target
    folds into ``None``, leaving the intent set (pinned by
    ``tests/test_intended_migration_apply.py::test_forced_target_retired_at_consult_is_skipped``)."""
    intent = chunk.intended_migration
    if intent is None:
        return None
    target = services.graphs.get(intent.graph_id)
    if target is None or services.graphs.is_retired(target.graph_id):
        return None
    return target


def _resolve_follow_latest_target(
    services: HubServices, chunk: Chunk, graph: Graph, *, hub_default: bool
) -> Graph | None:
    """The newer same-name mint a follow-latest chunk drifts to, or ``None`` (issue #164) — the policy
    is a no-op when the chunk carries an explicit ``intended_migration`` (which wins outright), when the
    effective policy resolves ``false`` (the graph's own tri-state, else ``hub_default``), or when the
    name resolves to nothing or to a mint not strictly newer than the chunk's own. Resolved at the edge
    so the apply service stays a taker-of-objects (``bzh:domain-takes-objects``)."""
    if chunk.intended_migration is not None:
        return None
    if not FollowLatest.of(services.graphs.follow_latest(graph.graph_id), hub_default=hub_default).enabled:
        return None
    newest = services.graphs.get_enabled_by_name(graph.name)
    if newest is None or not Mint.of(newest).newer_than(Mint.of(graph)):
        return None
    return newest


def _resolve_cross_graph_target(services: HubServices, graph: Graph, submission: CompletionSubmission) -> Graph | None:
    """The target graph a cross-graph migration edge (issue #90) names, resolved by name — ``None`` when
    the edge is not cross-graph or its ``graph:<name>`` names no enabled graph. Deliberately **total**:
    a missing node/edge/choice, or a retired target (issue #101), returns ``None`` rather than raising,
    so the apply failure path stays the one authoritative one. Pinned by
    ``tests/test_migration_apply.py::test_an_unresolvable_cross_graph_target_escalates_to_needs_human``."""
    from_node = graph.node_by_id(submission.from_node_id)
    if from_node is None:
        return None
    edge = graph.edge_for_choice(from_node.node_id, submission.choice)
    if edge is None or edge.target_graph is None:
        return None
    return services.graphs.get_enabled_by_name(edge.target_graph)


# Fleet-side counterparts — delegate to the shared rendering, never duplicate it.


@router.get("/queue/peek", response_model=QueuePeekResponse)
def peek_queue(services: Annotated[HubServices, Depends(get_services)]) -> QueuePeekResponse:
    """The runner's FILL read — the same ready queue as the board's own peek."""
    return queue_api.get_queue(services)


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkDetail:
    """The runner's chunk-status poll — the same aggregate as the board's own read."""
    return chunks_api.get_chunk(chunk_id, services)


@router.get("/chunks/{chunk_id}/work-items", response_model=WorkItemsView)
def get_work_items(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> WorkItemsView:
    """The chunk's work items, read with a runner's own bearer token — the same rendering as the
    anonymous operator route."""
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
    """Resume the chunk with a runner's own bearer token (issue #185) — see :func:`pause_chunk`."""
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
    """Drive a chunk parked at a generic hub command node one step (#65), running
    :class:`~blizzard.hub.delivery.hub_node.HubNodeExecutor` once under the fleet-wide serialization
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
    prev_status = facts.status().value
    epoch = facts.latest_epoch() or 0
    result = services.hub_node.run(chunk, graph, node, epoch=epoch)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    derived = facts.status()
    # `key` names the transition this call recorded — absent when the poll deferred or wrote a
    # poll-attempt fact instead, since there is no fresh `transitions` row to key on (issue #213).
    advance_key = f"transitions:{result.transition_id}" if result is not None and result.transition_id else None
    chunk_events.publish_chunk_changed(
        services, chunk_id, cause="hub-advanced", prev_status=prev_status, key=advance_key
    )
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
    prev_status = chunk_events.snapshot_chunk_status(services, chunk.chunk_id)
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
    # `running` (see `chunk_events.publish_chunk_changed`'s docstring).
    chunk_events.publish_chunk_changed(
        services,
        chunk.chunk_id,
        cause="claimed",
        prev_status=prev_status,
        status="running",
        key=f"route_created:{result.route_id}",
    )
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
    claim whose response was never read back. Confined to the live route's own runner via
    :func:`~blizzard.hub.api.auth.assert_owns`; this route presents no chunk-scoped ``route_token`` of
    its own, which is exactly what it is minting."""
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
    target_graph = _resolve_cross_graph_target(services, graph, submission)
    # Must precede apply() below — after apply() this always answers True, silencing the
    # publish_queue_changed() fresh-migration check further down.
    already_migrated = services.chunks.accepted_migration(
        chunk_id, from_node_id=submission.from_node_id, epoch=submission.epoch
    )
    intended_target_graph = _resolve_intended_migration_target(services, chunk)
    follow_latest_graph = _resolve_follow_latest_target(services, chunk, graph, hub_default=fleet.follow_latest)
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    result = services.apply.apply(
        chunk,
        graph,
        submission,
        route_token_mode=fleet.route_token_mode,
        produces_mode=fleet.produces_mode,
        target_graph=target_graph,
        intended_target_graph=intended_target_graph,
        follow_latest_graph=follow_latest_graph,
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
    chunk_events.publish_chunk_changed(services, chunk_id, cause=cause, prev_status=prev_status, key=key)
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
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    result = services.decisions.submit(chunk, graph, submission, route_token_mode=fleet.route_token_mode)
    key = f"decisions:{result.decision_id}" if result.decision_id is not None else None
    chunk_events.publish_chunk_changed(services, chunk_id, cause="decision-submitted", prev_status=prev_status, key=key)
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
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    escalation_id = services.runner_facts.record_escalation(
        chunk_id,
        epoch=report.epoch,
        takeover_command=report.takeover_command,
        wrapped_takeover_command=report.wrapped_takeover_command,
    )
    chunk_events.publish_chunk_changed(
        services, chunk_id, cause="escalated", prev_status=prev_status, key=f"escalations:{escalation_id}"
    )
    return {"chunk_id": chunk_id}


@router.post("/events", response_model=RunnerFactAck)
def ingest_runner_facts(
    batch: RunnerFactBatch,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> RunnerFactAck:
    """Land runner-minted facts — ``FactIngestService.ingest`` owns the seq high-water idempotence,
    :class:`IngestBroadcast` what each freshly-applied fact re-broadcasts on the SSE stream."""
    fleet.assert_owns(batch.runner_id)
    broadcast = IngestBroadcast.before_ingest(services, batch)
    result = services.facts.ingest(batch, route_token_mode=fleet.route_token_mode)
    broadcast.publish(result)
    return result.ack


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
