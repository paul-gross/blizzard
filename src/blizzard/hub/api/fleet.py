"""The runner-facing fleet router — every runner->hub call under ``/api/fleet/*``
(issue #87).

Enforcement is structural, not per-route: ``dependencies=[Depends(require_runner_principal)]``
on this router (attached once in :func:`~blizzard.hub.app.create_app`, not repeated per
route) means a fleet verb is authenticated *because of where it is mounted* — the same
``warn``/``enforce`` rollout posture :mod:`blizzard.hub.api.auth` already defines
(``warn``, the default, logs a missing/invalid token and lets the call proceed;
``enforce`` rejects it with 401). A route whose body/path declares its own ``runner_id``
additionally calls :func:`~blizzard.hub.api.auth.assert_owns` against the resolved
principal, so a fleet write for another runner's chunk/registration is rejected (403
under ``enforce``, warn-logged under ``warn``) rather than merely authenticated.

Two shapes of route live here:

* **Moved wholesale** — a verb only a runner ever called (claim, completion, decision,
  lease, escalation, envelope read, event push, registration, heartbeat, the runner's own
  pull read, the hub-command-node advance) is defined here outright; it no longer exists
  at its old anonymous path. ``hub-advance`` (#65/#66) postdates issue #87's own route
  inventory — driven by the runner's ADVANCE poll (``ctx.hub.hub_advance``), never the
  board or CLI, so it belongs here on the same "runner-only write" grounds as the rest of
  this list.
* **Fleet-side counterparts** — a read both the board and the runner need
  (``GET /chunks/{id}``, ``GET /chunks/{id}/pm-items``, ``GET /queue/peek``,
  ``GET /questions/{id}``) keeps its anonymous operator route right where it was
  (:mod:`blizzard.hub.api.chunks`, :mod:`blizzard.hub.api.queue`,
  :mod:`blizzard.hub.api.questions`) and gains a second, fleet-mounted route here that
  delegates to the very same rendering — never opening the operator read to a runner
  token, and never duplicating the logic.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunks as chunks_api
from blizzard.hub.api import questions as questions_api
from blizzard.hub.api import queue as queue_api
from blizzard.hub.api import runners as runners_api
from blizzard.hub.api.auth import RunnerPrincipal, assert_owns, require_runner_principal
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.delivery.hub_node import poll_interval_for
from blizzard.hub.domain.claim import ClaimConflict, ClaimDeniedPaused, ClaimDeniedTerminal
from blizzard.hub.domain.envelope import addendum_for_transition, build_node_envelope
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import (
    ChunkFacts,
    current_node_id,
    derive_chunk_status,
    hub_node_pending,
    latest_epoch,
    newest_transition,
)
from blizzard.wire.chunk import ChunkDetail, HubAdvanceResponse, PmItemsView
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyOutcome, ApplyResponse, NodeEnvelope
from blizzard.wire.facts import (
    QUESTION_ASKED,
    RUNNER_LOCALLY_PAUSED,
    RUNNER_LOCALLY_RESUMED,
    EscalationReport,
    LeaseMintReport,
    RunnerFactAck,
    RunnerFactBatch,
)
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


def _mode(request: Request) -> str:
    return request.app.state.config.runner_auth_mode


def _route_token_mode(request: Request) -> str:
    return request.app.state.config.route_token_mode


def _produces_mode(request: Request) -> str:
    return request.app.state.config.produces_mode


def _resolve_cross_graph_target(services: HubServices, graph: Graph, submission: CompletionSubmission) -> Graph | None:
    """The target graph a cross-graph migration edge (issue #90) names, resolved by name
    via the read graph repository — or ``None`` when the chosen edge is not cross-graph
    or its ``graph:<name>`` names no enabled graph.

    Resolved at the edge so :class:`~blizzard.hub.domain.apply.ApplyService` stays a pure
    taker-of-objects holding no graph repo (``bzh:domain-takes-objects``, MUST-FIX 2), the
    codebase's own "controller resolves the graph, passes it in" convention. Deliberately
    **total** (A3): a missing node/edge/choice returns ``None`` — it never raises, since
    those are ``apply()``'s authoritative ``_failure`` returns, not a second validation
    site that would 500 the controller. ``get_enabled_by_name`` folds a **retired**
    target into this same ``None`` bucket (issue #101): a chunk mid-workflow taking a
    migration edge whose named target has since been retired degrades to the same
    apply-failure path as a target that was never minted, rather than a distinct
    refusal — an explicit choice, not an oversight, though it means a retired
    cross-graph target is indistinguishable from an unminted one from the chunk's own
    apply-failure detail."""
    from_node = graph.node_by_id(submission.from_node_id)
    if from_node is None:
        return None
    edge = graph.edge_for_choice(from_node.node_id, submission.choice)
    if edge is None or edge.target_graph is None:
        return None
    return services.graphs.get_enabled_by_name(edge.target_graph)


# --------------------------------------------------------------------------- #
# Fleet-side counterparts of a board-facing read — delegate, never duplicate.
# --------------------------------------------------------------------------- #


@router.get("/queue/peek", response_model=QueuePeekResponse)
def peek_queue(services: Annotated[HubServices, Depends(get_services)]) -> QueuePeekResponse:
    """The runner's FILL read — the same ready queue as the board's own peek."""
    return queue_api.peek_queue(services)


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkDetail:
    """The runner's chunk-status poll — the same aggregate as the board's own read."""
    return chunks_api.get_chunk(chunk_id, services)


@router.get("/chunks/{chunk_id}/pm-items", response_model=PmItemsView)
def get_pm_items(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> PmItemsView:
    """The build worker's PM-items proxy target (via the runner-local pass-through,
    ``blizzard.runner.api.pm_items``), forwarded here with the runner's own bearer
    token — the same pass-through the board reads anonymously."""
    return chunks_api.get_pm_items(chunk_id, services)


@router.get("/questions/{question_id}", response_model=QuestionView)
def get_question(question_id: str, services: Annotated[HubServices, Depends(get_services)]) -> QuestionView:
    """The runner's answer poll before it resumes the dormant session."""
    row = services.chunks.get_question(question_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown question {question_id}")
    return questions_api.question_view(row)


# --------------------------------------------------------------------------- #
# Moved wholesale — no anonymous caller ever reached these.
# --------------------------------------------------------------------------- #


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
    node_id = current_node_id(facts) or graph.entry_node_id
    node = graph.node_by_id(node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="chunk has no current runner node (terminal)")
    return build_node_envelope(
        chunk=chunk,
        node=node,
        artifacts=services.chunks.load_artifacts(chunk_id),
        epoch=latest_epoch(facts) or 0,
        arrival_addendum=addendum_for_transition(graph, newest_transition(facts)),
    )


@router.post("/chunks/{chunk_id}/hub-advance", response_model=HubAdvanceResponse)
def hub_advance(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
) -> HubAdvanceResponse:
    """Drive a chunk parked at a generic hub command node one step (#65).

    Runs :class:`~blizzard.hub.delivery.hub_node.HubNodeExecutor` once, respecting the
    fleet-wide serialization slot: ``ran=False`` means a different chunk holds the
    slot right now, OR (#66) the node reported ``pending`` on a prior call and
    ``poll_interval`` has not yet elapsed — either way not an error, the runner's
    ADVANCE poll (``_advance_held_chunk``) simply calls this again on a later tick. A
    no-op (``ran=False``, ``detail`` names it) when the chunk is not currently parked
    at a generic hub command node — every hub node is this shape since #67; no other
    delivery route remains.

    No ``runner_id`` is declared on this request (it carries only ``chunk_id``), so
    the router-level ``require_runner_principal`` dependency is the whole check here —
    no :func:`~blizzard.hub.api.auth.assert_owns` call, the same shape as the other
    chunk-scoped fleet reads (``get_chunk``/``get_envelope``) that carry no runner_id
    to confine against.
    """
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    node_id = current_node_id(facts)
    node = graph.node_by_id(node_id) if node_id is not None else None
    if node is None or not node.is_hub_command_node:
        derived = derive_chunk_status(facts)
        return HubAdvanceResponse(
            chunk_id=chunk_id, status=derived, ran=False, detail="not parked at a hub command node"
        )
    epoch = latest_epoch(facts) or 0
    result = services.hub_node.run(chunk, graph, node, epoch=epoch)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    derived = derive_chunk_status(facts)
    services.events.publish_chunk_changed(chunk_id, derived.value)
    if result is None:
        pending = hub_node_pending(facts)
        next_poll_at = pending.polled_at + poll_interval_for(node) if pending is not None else None
        # `next_poll_at` in the future distinguishes "not yet due to poll" (#66, gated
        # before the slot was even attempted) from a genuinely busy slot — a pending
        # node whose interval already elapsed but lost the slot race falls through to
        # the busy message, same as a fresh hub node would.
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
    http_request: Request,
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> object:
    """Claim a chunk; 403 if the runner is paused at the hub, 409 if already claimed
    or already terminal ({done, stopped}, issue #118), else the first node envelope."""
    assert_owns(principal, claim.runner_id, mode=_mode(http_request))
    chunk = services.chunks.get(claim.chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {claim.chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
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
    services.events.publish_chunk_changed(chunk.chunk_id, "running")
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
    http_request: Request,
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> RouteTokenRekeyResponse:
    """Rotate the chunk's live route capability token (issue #84b) — the lost-plaintext
    recovery for a runner that crashed between the mint and reading the claim response
    back (``_adopt_interrupted_claim``, ``runner/loop/steps.py``). Confined to the
    live route's own runner via :func:`~blizzard.hub.api.auth.assert_owns`, the same
    ownership check every other fleet write makes — this route carries no chunk-scoped
    ``route_token`` of its own to present (that is exactly what it is minting)."""
    route = services.chunks.route_of(chunk_id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"chunk {chunk_id} has no live route")
    assert_owns(principal, route.runner_id, mode=_mode(http_request))
    route_token = services.claim.rekey(route)
    return RouteTokenRekeyResponse(chunk_id=chunk_id, route_token=route_token)


@router.post("/chunks/{chunk_id}/completions", response_model=ApplyResponse)
def submit_completion(
    chunk_id: str,
    submission: CompletionSubmission,
    services: Annotated[HubServices, Depends(get_services)],
    http_request: Request,
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> ApplyResponse:
    """Apply a node-step's completion atomically; reply carries the next envelope."""
    assert_owns(principal, submission.runner_id, mode=_mode(http_request))
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
    response = services.apply.apply(
        chunk,
        graph,
        submission,
        route_token_mode=_route_token_mode(http_request),
        produces_mode=_produces_mode(http_request),
        target_graph=target_graph,
    )
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    if response.outcome is ApplyOutcome.MIGRATED and not already_migrated:
        services.events.publish_queue_changed()  # a fresh migration re-queued the chunk under the target graph
    # A completion landing on a human-judged node opens a graph gate: surface it.
    chunks_api.publish_open_decision(services, chunk_id)
    return response


@router.post("/chunks/{chunk_id}/decisions", response_model=ApplyResponse)
def submit_decision(
    chunk_id: str,
    submission: DecisionSubmission,
    services: Annotated[HubServices, Depends(get_services)],
    http_request: Request,
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> ApplyResponse:
    """Runner-config gate: park the chunk on a decision in place of a transition."""
    assert_owns(principal, submission.runner_id, mode=_mode(http_request))
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    response = services.decisions.submit(chunk, graph, submission, route_token_mode=_route_token_mode(http_request))
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    # The runner-config gate parked the chunk on an open decision: surface it.
    chunks_api.publish_open_decision(services, chunk_id)
    return response


@router.post("/chunks/{chunk_id}/leases", status_code=status.HTTP_202_ACCEPTED)
def report_lease(
    chunk_id: str,
    report: LeaseMintReport,
    services: Annotated[HubServices, Depends(get_services)],
    http_request: Request,
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> dict[str, str]:
    """Land a runner's ``lease.minted`` — keeps the epoch fence in lockstep."""
    assert_owns(principal, report.runner_id, mode=_mode(http_request))
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    services.runner_facts.record_lease_minted(chunk_id, epoch=report.epoch, runner_id=report.runner_id)
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/escalations", status_code=status.HTTP_202_ACCEPTED)
def report_escalation(
    chunk_id: str,
    report: EscalationReport,
    services: Annotated[HubServices, Depends(get_services)],
    http_request: Request,
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> dict[str, str]:
    """Land a runner's ``escalation.recorded`` — the chunk derives ``needs_human``."""
    assert_owns(principal, report.runner_id, mode=_mode(http_request))
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    services.runner_facts.record_escalation(chunk_id, epoch=report.epoch, takeover_command=report.takeover_command)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    return {"chunk_id": chunk_id}


@router.post("/events", response_model=RunnerFactAck)
def ingest_runner_facts(
    batch: RunnerFactBatch,
    services: Annotated[HubServices, Depends(get_services)],
    http_request: Request,
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> RunnerFactAck:
    """Land runner-minted facts, idempotent by per-runner seq high-water.

    The store-and-forward ingest: ``lease.minted`` (the fence input), ``escalation.recorded``,
    ``question.asked``, and ``answer.delivered`` ride the runner's outbound buffer here. A
    pushed seq at or below the runner's high-water mark is already-applied and re-acked; a
    fresh one is applied and advances the mark. Each freshly-applied fact re-broadcasts on
    the SSE stream so the board refreshes — ``chunk-changed`` for every touched chunk, and
    ``question-asked`` for a forwarded ask.
    """
    assert_owns(principal, batch.runner_id, mode=_mode(http_request))
    ack = services.facts.ingest(batch, route_token_mode=_route_token_mode(http_request))
    if ack.applied:
        applied = set(ack.applied)
        for fact in batch.facts:
            if fact.seq not in applied:
                continue
            # Runner-scoped facts (issue #43) carry no chunk_id: they are about the runner,
            # so they refresh the fleet column, not a card. Handled before the chunk branch
            # below, which would otherwise skip them and land them invisibly — applied to
            # the store but never pushed, so the board would keep showing a runner as
            # claiming until something unrelated forced a refetch.
            if fact.kind in (RUNNER_LOCALLY_PAUSED, RUNNER_LOCALLY_RESUMED):
                services.events.publish_runner_changed(batch.runner_id)
                continue
            chunk_id = fact.payload.get("chunk_id")
            if not isinstance(chunk_id, str):
                continue
            if fact.kind == QUESTION_ASKED:
                question_id = fact.payload.get("question_id")
                if isinstance(question_id, str):
                    services.events.publish_question_asked(chunk_id, question_id)
            facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
            services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    return ack


@router.post("/runners", response_model=RunnerRegistrationResponse, status_code=status.HTTP_201_CREATED)
def register_runner(
    request: RunnerRegistrationRequest,
    http_request: Request,
    services: Annotated[HubServices, Depends(get_services)],
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> RunnerRegistrationResponse:
    """Register a runner — runner id + workspace binding; idempotent upsert.

    Runner-auth checked (issue #86a): ``warn`` (the default) logs and proceeds on a
    missing/invalid/mismatched token; ``enforce`` rejects."""
    assert_owns(principal, request.runner_id, mode=_mode(http_request))
    first = services.fleet.register(request.runner_id, request.workspace_id, env_capacity=request.env_capacity)
    services.events.publish_runner_changed(request.runner_id)
    return RunnerRegistrationResponse(runner_id=request.runner_id, first_registration=first)


@router.post("/runners/{runner_id}/heartbeats", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat_runner(
    runner_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    http_request: Request,
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> Response:
    """Refresh a runner's liveness — the slow runner-level heartbeat. Returns 204."""
    assert_owns(principal, runner_id, mode=_mode(http_request))
    if not services.fleet.heartbeat(runner_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    services.events.publish_runner_changed(runner_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runners/{runner_id}", response_model=RunnerView)
def get_runner(
    runner_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    http_request: Request,
    principal: Annotated[RunnerPrincipal | None, Depends(require_runner_principal)],
) -> RunnerView:
    """One runner's declarative state — the runner's own pull read."""
    assert_owns(principal, runner_id, mode=_mode(http_request))
    liveness = services.fleet.get_liveness(runner_id)
    if liveness is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    return runners_api.runner_view(liveness)
