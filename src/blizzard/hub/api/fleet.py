"""The runner-facing fleet router — every runner->hub call under ``/api/fleet/*`` but the token-identity read.

Enforcement is structural: the router's own ``dependencies`` authenticate a fleet verb *because of where it
is mounted*, and the caller is always the runner its bearer token names (:meth:`FleetRequest.caller_id`) — a
``runner_id`` a request body carries is never read. A route addressing a runner or a route by id confines it
to the caller through :meth:`FleetRequest.assert_owns`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import collaborator, dto
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events, node_steps
from blizzard.hub.api import chunk_statuses as chunk_statuses_api
from blizzard.hub.api import chunks as chunks_api
from blizzard.hub.api import questions as questions_api
from blizzard.hub.api import queue as queue_api
from blizzard.hub.api import runners as runners_api
from blizzard.hub.api import system_artifacts as system_artifacts_api
from blizzard.hub.api import transcripts as transcripts_api
from blizzard.hub.api.analytics import (
    EventScopeFilters,
    ScopeFilters,
    counts_response,
    named_counts,
    named_spend,
    operational_criteria,
    spend_response,
)
from blizzard.hub.api.auth import RunnerPrincipal, require_runner_principal
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.findings import finding_view
from blizzard.hub.api.garden_proposals import garden_proposal_view
from blizzard.hub.api.ingest_broadcast import IngestBroadcast, pushed_facts
from blizzard.hub.api.scopes import scope_view
from blizzard.hub.composition import HubServices
from blizzard.hub.config import HubConfig
from blizzard.hub.delivery.hub_node import PollPolicy
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
)
from blizzard.hub.domain.execution.claim import (
    ClaimConflict,
    ClaimDeniedDependency,
    ClaimDeniedIncompatible,
    ClaimDeniedNotReady,
    ClaimDeniedPaused,
    ClaimDeniedTerminal,
    ClaimDeniedUnregistered,
    RekeyDeniedTerminal,
)
from blizzard.hub.domain.execution.completion import MigrationTargets
from blizzard.hub.domain.execution.envelope import Envelope, NoCurrentNode
from blizzard.hub.domain.execution.submissions import Completion
from blizzard.hub.domain.garden.proposals.model import RoutineProposalState
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import FollowLatest, Graph
from blizzard.hub.domain.observability.transcripts import LeaseSegmentsNotOwned, refuse_foreign_lease_read
from blizzard.hub.domain.runners.registration import (
    DeclaredSubscription,
    RunnerCapability,
    RunnerNeverConnected,
    RunnerRetired,
)
from blizzard.wire.analytics import AnalyticsCountsResponse, AnalyticsSpendResponse
from blizzard.wire.chunk import (
    ChunkDetail,
    ChunkPauseRequest,
    ChunkStatusView,
    ChunkSummary,
    HubAdvanceResponse,
    WorkItemsView,
)
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import (
    RunnerFactAck,
    RunnerFactBatch,
)
from blizzard.wire.finding import FindingView
from blizzard.wire.fleet import FleetSummaryView
from blizzard.wire.garden_proposal import GardenProposalView
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekRequest, QueuePeekResponse
from blizzard.wire.route import (
    RouteClaim,
    RouteClaimConflict,
    RouteClaimDependencyDenial,
    RouteClaimIncompatibleDenial,
    RouteClaimPausedDenial,
    RouteClaimResponse,
    RouteClaimTerminalDenial,
    RouteTokenRekeyResponse,
)
from blizzard.wire.runner import RunnerRegistrationRequest, RunnerRegistrationResponse, RunnerView
from blizzard.wire.scope import ScopeView
from blizzard.wire.system_artifact import SystemArtifactView
from blizzard.wire.transcript_segment import LeaseTranscriptView, TranscriptSegmentAck, TranscriptSegmentBatch

_log = get_logger("blizzard.hub.fleet")

router = APIRouter(prefix="/api/fleet", tags=["fleet"], dependencies=[Depends(require_runner_principal)])


@collaborator
@dataclass(frozen=True)
class FleetRequest:
    """One fleet-router call: the runner its bearer token names, and the hub policy it is judged under."""

    principal: RunnerPrincipal
    config: HubConfig

    @classmethod
    def of(
        cls,
        request: Request,
        principal: Annotated[RunnerPrincipal, Depends(require_runner_principal)],
    ) -> FleetRequest:
        return cls(principal, request.app.state.config)

    @property
    def route_token_mode(self) -> str:
        return self.config.route_token_mode

    @property
    def produces_mode(self) -> str:
        return self.config.produces_mode

    @property
    def follow_latest(self) -> bool:
        return bool(self.config.follow_latest)

    def caller_id(self) -> str:
        """The calling runner's id — the bearer-token principal's, the one identity a fleet call
        carries; the gate has already refused a call whose token resolved to no runner."""
        return self.principal.runner_id

    def assert_owns(self, runner_id: str) -> None:
        """Refuse with 403 a call on a runner, or a runner's route, other than the caller's own."""
        if self.principal.runner_id == runner_id:
            return
        _log.warning("runner_id mismatch", declared_runner_id=runner_id, token_runner_id=self.principal.runner_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"token belongs to runner {self.principal.runner_id!r}, not {runner_id!r}",
        )


def _demand_lease_owner(principal: RunnerPrincipal, owning_runner_id: str | None) -> None:
    """The lease-transcript read route's ownership gate, mapped — raises on a mismatch;
    :func:`refuse_foreign_lease_read` decides."""
    try:
        refuse_foreign_lease_read(owning_runner_id, requesting_runner_id=principal.runner_id)
    except LeaseSegmentsNotOwned as exc:
        # The owning runner's id stays out of the response — logged server-side instead,
        # where an operator, not another runner, can see it.
        _log.warning(
            "lease-transcript ownership mismatch",
            owning_runner_id=exc.owning_runner_id,
            requesting_runner_id=exc.requesting_runner_id,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc


def _migration_targets(
    services: HubServices, chunk: Chunk, graph: Graph, completion: Completion, *, follow_latest_default: bool
) -> MigrationTargets:
    """Load the graphs one completion may move the chunk onto; :class:`MigrationTargets` decides."""
    graphs = services.graphs
    cross_graph_name = MigrationTargets.cross_graph_name(graph, completion)
    intent = chunk.intended_migration
    intent_graph = graphs.get(intent.graph_id) if intent is not None else None
    return MigrationTargets.of(
        chunk,
        graph,
        cross_graph=graphs.get_enabled_by_name(cross_graph_name) if cross_graph_name is not None else None,
        intent_graph=intent_graph,
        intent_retired=intent_graph is not None and graphs.is_retired(intent_graph.graph_id),
        follow_latest=FollowLatest.of(graphs.follow_latest(graph.graph_id), hub_default=follow_latest_default),
        newest_same_name=graphs.get_enabled_by_name(graph.name) if intent is None else None,
    )


# Fleet-side counterparts — delegate to the shared rendering, never duplicate it.


@router.get("/queue/peek", response_model=QueuePeekResponse)
def peek_queue(services: Annotated[HubServices, Depends(get_services)]) -> QueuePeekResponse:
    """The runner's FILL read — the whole ready-queue order: a filling runner needs every
    ready chunk in one read. Kept as-is for a previous-minor caller;
    ``POST /queue/peek`` below is the matched counterpart."""
    statuses = services.chunks.facts.load_live_statuses()
    return queue_api.ReadyQueue.of(services, statuses).view


@router.post("/queue/peek", response_model=QueuePeekResponse)
def peek_matched_queue(
    request: QueuePeekRequest,
    services: Annotated[HubServices, Depends(get_services)],
    principal: Annotated[RunnerPrincipal, Depends(require_runner_principal)],
) -> QueuePeekResponse:
    """The matched fleet peek — at most one ready entry the calling principal can both
    work (declared capabilities against ``EligibilityCheck``) and claim (not
    dependency-blocked), with ``request.policy`` applied to both."""
    statuses = services.chunks.facts.load_live_statuses()
    return queue_api.MatchedPeek.of(services, statuses, request).view


@router.get("/system-artifacts", response_model=list[SystemArtifactView])
def list_system_artifacts_route(services: Annotated[HubServices, Depends(get_services)]) -> list[SystemArtifactView]:
    """The full ``system``-scoped artifact set — resolved at call time off the packaged
    set, pinned to no chunk or lease."""
    return system_artifacts_api.list_system_artifacts(services.system_artifacts)


@router.get("/system-artifacts/{name:path}", response_model=SystemArtifactView)
def get_system_artifact_route(name: str, services: Annotated[HubServices, Depends(get_services)]) -> SystemArtifactView:
    """One published system artifact by its slash-bearing name; ``404`` unknown."""
    return system_artifacts_api.get_system_artifact(name, services.system_artifacts)


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkDetail:
    """The runner's chunk-status poll — the same aggregate as ``GET /api/chunks/{chunk_id}``."""
    return chunks_api.get_chunk(chunk_id, services)


@router.get("/chunk-statuses", response_model=list[ChunkStatusView])
def get_chunk_statuses(
    chunk_id: Annotated[list[str], Query()], services: Annotated[HubServices, Depends(get_services)]
) -> list[ChunkStatusView]:
    """The runner tick's slim batch status read — repeatable ``chunk_id``;
    an unknown or ephemeral id is omitted, never a 404."""
    return chunk_statuses_api.chunk_statuses(chunk_id, services)


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
        "Deprecated: use `GET /fleet/chunks/{chunk_id}/work-items`, which "
        "this path aliases onto the identical handler and returns the identical view."
    ),
)


@router.post("/chunks/{chunk_id}/pause", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def pause_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
    """Pause the chunk with a runner's own bearer token — the same transition as the
    operator route, ``by`` defaulting to ``operator``."""
    return chunks_api.pause_chunk(chunk_id, ChunkPauseRequest(), services)


@router.post("/chunks/{chunk_id}/resume", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def resume_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
    """Resume the chunk with a runner's own bearer token. Takes no body, so the
    resume is always recorded as ``operator``."""
    return chunks_api.resume_chunk(chunk_id, ChunkPauseRequest(), services)


@router.get("/summary", response_model=FleetSummaryView)
def fleet_summary(services: Annotated[HubServices, Depends(get_services)]) -> FleetSummaryView:
    """The fleet-pulse counts, read with a runner's own bearer token. Fleet-router-only:
    this read has no anonymous counterpart."""
    return chunks_api.FleetPulse(services).view()


@router.get("/questions/{question_id}", response_model=QuestionView)
def get_question(question_id: str, services: Annotated[HubServices, Depends(get_services)]) -> QuestionView:
    """The runner's answer poll before it resumes the dormant session."""
    row = services.chunks.questions.get_question(question_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown question {question_id}")
    return questions_api.question_view(row, services.registry.names_for([row.runner_id]))


# Moved wholesale — no anonymous caller ever reached these.


@router.get("/chunks/{chunk_id}/envelope", response_model=NodeEnvelope)
def get_envelope(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> NodeEnvelope:
    """The chunk's current node envelope, idempotent — the lost-apply re-read."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    facts = ChunkFacts.or_default(services.chunks.facts.load_facts(chunk_id))
    try:
        envelope = Envelope.current(
            chunk, graph, facts, services.chunks.artifacts.load_artifacts(chunk_id), label=services.work_ref_label
        )
    except NoCurrentNode as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return node_steps.node_envelope(envelope)


def _routine_run_or_404(chunk_id: str, services: HubServices) -> RunContext:
    """The run context that gates a worker's fleet-scoped read — 404 both for an
    unknown chunk and for one carrying no run context (not a routine run), returning the
    chunk's :class:`RunContext`."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    run = services.run_context.for_chunk(chunk)
    if run is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"chunk {chunk_id} carries no run context — not a routine run",
        )
    return run


@router.get("/chunks/{chunk_id}/garden/findings", response_model=list[FindingView])
def get_garden_findings(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> list[FindingView]:
    """A worker's finding bucket — exactly what its delivery may cite (`FindingBucket`); the
    chunk's own run context derives the routine and scope, no caller flag can name another.
    404 for an unknown chunk or one with no run context (not a routine run)."""
    run = _routine_run_or_404(chunk_id, services)
    return [finding_view(f) for f in services.finding_bucket.for_run(run).citable]


@router.get("/chunks/{chunk_id}/garden/proposals", response_model=list[GardenProposalView])
def get_garden_proposals(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    state: Annotated[RoutineProposalState, Query()] = RoutineProposalState.OPEN,
) -> list[GardenProposalView]:
    """A worker's own routine's garden proposals, filtered by `state` (`open` by
    default, plus `closed` and `all`, each closed entry carrying its closure) — the
    chunk's own run context derives the routine; no caller-supplied flag can name
    another, and no scope filter applies (a proposal carries no scope column). 404 both
    for an unknown chunk and for one carrying no run context (not a routine run): a
    chunk with nothing to read is refused rather than answered with an empty bucket."""
    run = _routine_run_or_404(chunk_id, services)
    return [
        garden_proposal_view(p, closure)
        for p, closure in services.routine_garden_proposals.list_for_routine(run.routine_name, state)
    ]


@dto
@dataclass(frozen=True)
class AnalyticsWindow:
    """The one filter a worker's fleet-scoped analytics read takes: the window
    — ``since`` required (422 unset), ``until`` optional, both UTC-aware
    instants (``bzh:utc-instants``). No graph, source, or event-shape filter: those stay
    the operator plane's own. Builds the operator plane's own filter types with only the
    window populated, so a fleet route renders through the identical criteria and
    response-shaping helpers the operator route does."""

    since: datetime
    until: datetime | None

    @classmethod
    def of(
        cls,
        since: Annotated[datetime, Query()],
        until: Annotated[datetime | None, Query()] = None,
    ) -> AnalyticsWindow:
        return cls(since, until)

    @property
    def scope(self) -> ScopeFilters:
        return ScopeFilters(graph_id=None, source=None, since=self.since, until=self.until)

    @property
    def event_scope(self) -> EventScopeFilters:
        return EventScopeFilters(
            self.scope, extractor_version=None, harness_id=None, harness_version=None, model=None, effort=None
        )


@router.get("/chunks/{chunk_id}/analytics/counts/files", response_model=AnalyticsCountsResponse)
def get_chunk_analytics_counts_files(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
) -> AnalyticsCountsResponse:
    """A worker's own routine-run read of ``GET /api/analytics/counts/files`` — the same
    rows, over the window it names, gated on the chunk carrying a run context rather than
    on operator credentials."""
    _routine_run_or_404(chunk_id, services)
    return counts_response(services.analytics_events.counts_by_file(window.event_scope.criteria))


@router.get("/chunks/{chunk_id}/analytics/counts/skills", response_model=AnalyticsCountsResponse)
def get_chunk_analytics_counts_skills(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
) -> AnalyticsCountsResponse:
    """A worker's own routine-run read of ``GET /api/analytics/counts/skills``."""
    _routine_run_or_404(chunk_id, services)
    return counts_response(services.analytics_events.counts_by_skill(window.event_scope.criteria))


@router.get("/chunks/{chunk_id}/analytics/counts/agent-types", response_model=AnalyticsCountsResponse)
def get_chunk_analytics_counts_agent_types(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
) -> AnalyticsCountsResponse:
    """A worker's own routine-run read of ``GET /api/analytics/counts/agent-types``."""
    _routine_run_or_404(chunk_id, services)
    return counts_response(services.analytics_events.counts_by_agent_type(window.event_scope.criteria))


@router.get("/chunks/{chunk_id}/analytics/counts/nodes", response_model=AnalyticsCountsResponse)
def get_chunk_analytics_counts_nodes(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
    by_name: Annotated[bool, Query()] = False,
) -> AnalyticsCountsResponse:
    """A worker's own routine-run read of ``GET /api/analytics/counts/nodes``."""
    _routine_run_or_404(chunk_id, services)
    return counts_response(named_counts(services.analytics_events.counts_by_node(window.event_scope.criteria), by_name))


@router.get("/chunks/{chunk_id}/analytics/spend/nodes", response_model=AnalyticsSpendResponse)
def get_chunk_analytics_spend_nodes(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
    by_name: Annotated[bool, Query()] = False,
) -> AnalyticsSpendResponse:
    """A worker's own routine-run read of ``GET /api/analytics/spend/nodes``."""
    _routine_run_or_404(chunk_id, services)
    stats = services.operational_analytics.spend_by_node(operational_criteria(window.scope))
    return spend_response(named_spend(stats, by_name))


@router.get("/chunks/{chunk_id}/analytics/spend/graphs", response_model=AnalyticsSpendResponse)
def get_chunk_analytics_spend_graphs(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
    by_name: Annotated[bool, Query()] = False,
) -> AnalyticsSpendResponse:
    """A worker's own routine-run read of ``GET /api/analytics/spend/graphs``."""
    _routine_run_or_404(chunk_id, services)
    stats = services.operational_analytics.spend_by_graph(operational_criteria(window.scope))
    return spend_response(named_spend(stats, by_name))


@router.get("/scopes", response_model=list[ScopeView])
def get_scopes(services: Annotated[HubServices, Depends(get_services)]) -> list[ScopeView]:
    """Every scope, newest first, each marked retired or not — the
    deployment's scope vocabulary."""
    return [scope_view(s) for s in services.scopes.list_all()]


def _answered_findings_or_404(chunk_id: str, services: HubServices) -> list[FindingView]:
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    findings = services.answered_findings.resolve_for_chunk(chunk)
    if findings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"chunk {chunk_id} answers no accepted, minted garden proposal",
        )
    return [finding_view(f) for f in findings]


@router.get("/chunks/{chunk_id}/findings", response_model=list[FindingView])
def get_chunk_findings(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> list[FindingView]:
    """The findings the chunk's own accepted, minted garden proposal answers — a worker's
    per-chunk read, distinct from ``get_garden_findings``'s routine-run bucket. 404 both
    for an unknown chunk and for one answering no such proposal."""
    return _answered_findings_or_404(chunk_id, services)


@router.get("/chunks/{chunk_id}/findings/{finding_id}", response_model=FindingView)
def get_chunk_finding(
    chunk_id: str, finding_id: str, services: Annotated[HubServices, Depends(get_services)]
) -> FindingView:
    """One finding within the chunk's own answered set — ``get_chunk_findings`` narrowed
    to a single id. An id outside that set is structurally unreachable rather than
    filtered after the fact, so it 404s the same as an id belonging to no proposal at
    all."""
    for finding in _answered_findings_or_404(chunk_id, services):
        if finding.finding_id == finding_id:
            return finding
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"finding {finding_id} is not among the findings chunk {chunk_id} answers",
    )


@router.post("/chunks/{chunk_id}/hub-advance", response_model=HubAdvanceResponse)
def hub_advance(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
) -> HubAdvanceResponse:
    """Drive a chunk parked at a generic hub command node one step, running that node's
    hub-side command once under the fleet-wide serialization slot. ``ran=False`` is never an
    error: a different chunk holds the slot, the node reported ``pending`` before ``poll_interval``
    elapsed, or the chunk is not parked at a hub command node it may drive — ``detail`` names which.
    The request declares no ``runner_id`` to confine against."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    facts = services.chunks.facts.load_facts(chunk_id) or ChunkFacts(minted=True)
    node = facts.hub_advance_node(graph)
    if node is None:
        return HubAdvanceResponse(
            chunk_id=chunk_id, status=facts.status(), ran=False, detail="not parked at a hub command node"
        )
    change = chunk_events.ChunkChanged.of(services, chunk_id, prev_status=facts.status().value)
    epoch = facts.latest_epoch() or 0
    result = services.hub_node.run(chunk, graph, node, epoch=epoch)
    facts = services.chunks.facts.load_facts(chunk_id) or ChunkFacts(minted=True)
    derived = facts.status()
    # `key` names the transition this call recorded — absent when the poll deferred or wrote a
    # poll-attempt fact instead, since there is no fresh `transitions` row to key on.
    advance_key = f"transitions:{result.transition_id}" if result is not None and result.transition_id else None
    change.publish(cause="hub-advanced", key=advance_key)
    if result is None:
        pending = facts.hub_node_pending()
        next_poll_at = pending.polled_at + PollPolicy.of(node).interval if pending is not None else None
        # A future `next_poll_at` distinguishes "not yet due to poll" from a genuinely busy slot;
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
    """Claim a chunk; 403 if the runner is unregistered, paused, or retired at the hub, 409 if already claimed,
    already terminal ({done, stopped}), not ready, standing on an unmet prerequisite,
    or incompatible with the runner's stored capabilities, else the first node envelope."""
    runner_id = fleet.caller_id()
    chunk = services.chunks.record.get(claim.chunk_id)
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
            runner_id=runner_id,
            workspace_id=claim.workspace_id,
            environment_ids=claim.environment_ids,
        )
    except RunnerRetired as exc:
        retired_denial = RouteClaimPausedDenial(chunk_id=claim.chunk_id, runner_id=exc.runner_id, detail=str(exc))
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=retired_denial.model_dump())
    except ClaimDeniedPaused as exc:
        denial = RouteClaimPausedDenial(chunk_id=claim.chunk_id, runner_id=exc.runner_id)
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=denial.model_dump())
    except ClaimDeniedUnregistered as exc:
        unregistered = RouteClaimPausedDenial(chunk_id=claim.chunk_id, runner_id=exc.runner_id, detail=str(exc))
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=unregistered.model_dump())
    except ClaimDeniedTerminal as exc:
        terminal_denial = RouteClaimTerminalDenial(chunk_id=claim.chunk_id, status=exc.status.value)
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=terminal_denial.model_dump())
    except ClaimDeniedNotReady as exc:
        # The status-carrying 409 shape: a runner skips the chunk as it does an ended one.
        not_ready_denial = RouteClaimTerminalDenial(chunk_id=claim.chunk_id, status=exc.status.value, detail=str(exc))
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=not_ready_denial.model_dump())
    except ClaimDeniedDependency as exc:
        dependency_denial = RouteClaimDependencyDenial(
            chunk_id=claim.chunk_id, prerequisite_chunk_id=exc.prerequisite_chunk_id
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=dependency_denial.model_dump())
    except ClaimDeniedIncompatible as exc:
        incompatible_denial = RouteClaimIncompatibleDenial(
            chunk_id=claim.chunk_id, incompatible_runner_id=exc.runner_id
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=incompatible_denial.model_dump())
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
        envelope=node_steps.node_envelope(result.envelope),
        route_token=result.route_token,
    )


@router.post("/chunks/{chunk_id}/route-token", response_model=RouteTokenRekeyResponse)
def rekey_route_token(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> RouteTokenRekeyResponse:
    """Rotate the chunk's live route capability token — the lost-plaintext recovery for a
    claim whose response was never read back. Confined to the live route's own runner; this route
    presents no chunk-scoped ``route_token`` of its own, which is exactly what it is minting. 403
    when the route's runner is retired; 409 when the chunk has ended."""
    route = services.chunks.route.route_of(chunk_id)
    if route is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"chunk {chunk_id} has no live route")
    fleet.assert_owns(route.runner_id)
    facts = ChunkFacts.or_default(services.chunks.facts.load_facts(chunk_id))
    try:
        route_token = services.claim.rekey(route, facts)
    except RekeyDeniedTerminal as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RouteTokenRekeyResponse(chunk_id=chunk_id, route_token=route_token)


@router.post("/chunks/{chunk_id}/completions", response_model=ApplyResponse)
def submit_completion(
    chunk_id: str,
    submission: CompletionSubmission,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> ApplyResponse:
    """Apply a node-step's completion atomically; reply carries the next envelope; 403 when the
    submitting runner is retired."""
    runner_id = fleet.caller_id()
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    completion = node_steps.completion_of(submission, runner_id=runner_id)
    targets = _migration_targets(services, chunk, graph, completion, follow_latest_default=fleet.follow_latest)
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    result = services.apply.apply(
        chunk,
        graph,
        completion,
        route_token_mode=fleet.route_token_mode,
        produces_mode=fleet.produces_mode,
        targets=targets,
    )
    fresh_migration = result.fresh_migration
    cause = "migrated" if fresh_migration else "node-completed"
    # `key` names the fact this call wrote, per each cause's own mapped fact table:
    # `migration_id` only for a genuine `migrated`, `transition_id` only when a fresh row backs it.
    if fresh_migration:
        key = f"chunk_migrations:{result.migration_id}"
    elif result.transition_id is not None:
        key = f"transitions:{result.transition_id}"
    else:
        key = None
    change.publish(cause=cause, key=key)
    if fresh_migration:
        services.events.publish_queue_changed()  # a fresh migration re-queued the chunk under the target graph
    # A completion landing on a human-judged node opens a graph gate: surface it.
    chunks_api.OpenDecision(services, chunk_id).publish()
    return node_steps.apply_response(result.outcome, detail=result.detail, envelope=result.envelope)


@router.post("/chunks/{chunk_id}/decisions", response_model=ApplyResponse)
def submit_decision(
    chunk_id: str,
    submission: DecisionSubmission,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> ApplyResponse:
    """Runner-config gate: park the chunk on a decision in place of a transition; 403 when the
    submitting runner is retired."""
    runner_id = fleet.caller_id()
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    result = services.decisions.submit(
        chunk,
        graph,
        node_steps.gate_submission_of(submission, runner_id=runner_id),
        route_token_mode=fleet.route_token_mode,
        produces_mode=fleet.produces_mode,
    )
    key = f"decisions:{result.decision_id}" if result.decision_id is not None else None
    change.publish(cause="decision-submitted", key=key)
    # The runner-config gate parked the chunk on an open decision: surface it.
    chunks_api.OpenDecision(services, chunk_id).publish()
    return node_steps.apply_response(result.outcome, detail=result.detail)


@router.post("/events", response_model=RunnerFactAck)
def ingest_runner_facts(
    batch: RunnerFactBatch,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> RunnerFactAck:
    """Land runner-minted facts — idempotent on the batch's per-runner ``seq`` high-water mark,
    with each freshly-applied fact re-broadcast on the SSE stream; 403 when the runner is retired."""
    runner_id = fleet.caller_id()
    runner_name = fleet.principal.runner_name
    broadcast = IngestBroadcast.before_ingest(services, runner_id, batch, runner_name=runner_name)
    result = services.facts.ingest(
        runner_id, pushed_facts(batch), route_token_mode=fleet.route_token_mode, runner_name=runner_name
    )
    broadcast.publish(result)
    return RunnerFactAck(
        runner_id=runner_id,
        high_water=result.high_water,
        applied=result.applied,
        already_applied=result.already_applied,
        rejected=result.rejected,
        route_ended=result.route_ended,
    )


@router.post("/transcripts", response_model=TranscriptSegmentAck)
def ingest_transcript_segments(
    batch: TranscriptSegmentBatch,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> TranscriptSegmentAck:
    """Land the runner's batched transcript records — the transcript lane's own
    store-and-forward push, distinct from the fact lane at ``POST /api/fleet/events``; 403 when the
    runner is retired."""
    runner_id = fleet.caller_id()
    records = [(record.seq, transcripts_api.to_domain_record(record, runner_id=runner_id)) for record in batch.records]
    result = services.transcript_ingest.ingest(runner_id, records)
    return transcripts_api.to_ack(runner_id, result)


@router.get("/chunks/{chunk_id}/transcript-segments", response_model=LeaseTranscriptView)
def get_lease_transcript_segments(
    chunk_id: str,
    node_id: str,
    epoch: int,
    services: Annotated[HubServices, Depends(get_services)],
    principal: Annotated[RunnerPrincipal, Depends(require_runner_principal)],
) -> LeaseTranscriptView:
    """A runner's read-back of its own shipped segments — every
    accepted record across every spawn generation under a lease's ``(chunk_id, node_id,
    epoch)``, confined against the ``runner_id`` already on those rows."""
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
    """Register the calling runner — the one its bearer token names — with its workspace binding
    and the name it declares; idempotent.

    The hub never rejects a registration over its roster — it doubles as the heartbeat every tick.
    A body ``runner_id`` from an older runner is ignored. A retired runner is refused 403."""
    runner_id = fleet.caller_id()
    registration = services.registry.get_runner(runner_id)
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    capabilities = tuple(
        RunnerCapability(
            harness_id=c.harness_id,
            version=c.version,
            tiers=tuple(c.tiers),
            default=c.default,
            available=c.available,
        )
        for c in request.capabilities
    )
    subscriptions = (
        tuple(DeclaredSubscription(slug=d.slug, name=d.name, provider=d.provider) for d in request.subscriptions)
        if request.subscriptions is not None
        else None
    )
    registered = services.fleet.register(
        registration,
        request.workspace_id,
        name=request.name,
        env_capacity=request.env_capacity,
        public_url=request.url,
        redirect_uris=tuple(request.redirect_uris),
        capabilities=capabilities,
        subscriptions=subscriptions,
        gates=tuple(request.gates),
    )
    services.events.publish_runner_changed(runner_id, kind="registered", runner_name=registered.name)
    return RunnerRegistrationResponse(
        runner_id=runner_id, runner_name=registered.name, first_registration=registered.first
    )


@router.post("/runners/{runner_id}/heartbeats", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat_runner(
    runner_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> Response:
    """Refresh the calling runner's liveness — the slow runner-level heartbeat. Returns 204; 403 when
    retired or when the path names another runner; 404 before its first registration."""
    fleet.assert_owns(runner_id)
    alive = services.fleet.heartbeat(runner_id)
    if not alive:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    services.events.publish_runner_changed(runner_id, kind="heartbeat", runner_name=fleet.principal.runner_name)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runners/{runner_id}", response_model=RunnerView)
def get_runner(
    runner_id: str,
    services: Annotated[HubServices, Depends(get_services)],
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> RunnerView:
    """The calling runner's own declarative state — its pull read; 403 when retired or when the path
    names another runner, 409 before its first registration."""
    fleet.assert_owns(runner_id)
    registration = services.registry.get_runner(runner_id)
    if registration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown runner {runner_id}")
    try:
        registration.refuse_if_never_connected()
    except RunnerNeverConnected as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return runners_api.runner_view(services.fleet.own_liveness(registration), now=services.clock.now())
