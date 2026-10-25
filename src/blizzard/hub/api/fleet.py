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

from blizzard.foundation.claim_denials import ClaimDenialReason
from blizzard.foundation.logging import get_logger
from blizzard.foundation.release_refusals import ReleaseRefusalReason
from blizzard.foundation.roles import dto
from blizzard.foundation.store.utc import as_utc, iso_utc
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
from blizzard.hub.api.auth import RunnerPrincipal, require_runner_principal, require_runner_principal_even_if_retired
from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.findings import finding_view
from blizzard.hub.api.garden_proposals import garden_proposal_view
from blizzard.hub.api.ingest_broadcast import IngestBroadcast, pushed_facts
from blizzard.hub.api.project_labels import ProjectLabels
from blizzard.hub.api.project_params import QueryProject
from blizzard.hub.api.scopes import scope_view
from blizzard.hub.composition import TenantServices
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
    ClaimDeniedVersionRefused,
    RekeyDeniedNotHolder,
    RekeyDeniedTerminal,
)
from blizzard.hub.domain.execution.completion import MigrationTargets
from blizzard.hub.domain.execution.envelope import Envelope, NoCurrentNode
from blizzard.hub.domain.execution.release import ReleaseRefusalKind, ReleaseRefused
from blizzard.hub.domain.execution.submissions import Completion
from blizzard.hub.domain.garden.proposals.model import RoutineProposalState
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import FollowLatest, Graph
from blizzard.hub.domain.kernel.pagination import DEFAULT_LIMIT, MAX_LIMIT, MalformedCursor
from blizzard.hub.domain.observability.transcripts import LeaseSegmentsNotOwned, refuse_foreign_lease_read
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.runners.bindings import flag_bindings, offered_projects
from blizzard.hub.domain.runners.registration import (
    DeclaredSubscription,
    RunnerBinding,
    RunnerCapability,
    RunnerDeclining,
    RunnerNeverConnected,
    RunnerRetired,
)
from blizzard.hub.domain.runners.version import RunnerVersionRefused
from blizzard.hub.tenancy import StoreScope
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
from blizzard.wire.garden_proposal import GardenProposalsPageView, GardenProposalView
from blizzard.wire.question import QuestionView
from blizzard.wire.queue import QueuePeekRequest, QueuePeekResponse
from blizzard.wire.release import ReleaseRefusal, ReleaseRequest, ReleaseResponse
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
from blizzard.wire.runner import (
    RunnerRegistrationRequest,
    RunnerRegistrationResponse,
    RunnerVersionRefusal,
    RunnerView,
    VersionWindowView,
)
from blizzard.wire.scope import ScopeView
from blizzard.wire.system_artifact import SystemArtifactView
from blizzard.wire.transcript_segment import LeaseTranscriptView, TranscriptSegmentAck, TranscriptSegmentBatch

_log = get_logger("blizzard.hub.fleet")

router = APIRouter(
    prefix="/api/fleet", tags=["fleet"], dependencies=[Depends(require_runner_principal), Depends(tenant_bound)]
)

#: The claim route's own router: gated by the principal *without* the retirement refusal, because a retired
#: runner's claim is refused in-domain with the paused-denial body older runners parse.
claim_router = APIRouter(
    prefix="/api/fleet",
    tags=["fleet"],
    dependencies=[Depends(require_runner_principal_even_if_retired), Depends(tenant_bound)],
)


@dataclass(frozen=True)
class FleetRequest:
    """One fleet-router call: the runner its bearer token names, the hub policy it is judged under,
    and the tenant that runner was added in — its name, and its services, the only ones the call
    reaches."""

    principal: RunnerPrincipal
    config: HubConfig
    scope: StoreScope
    tenant_name: str
    services: TenantServices

    @classmethod
    def of(
        cls,
        request: Request,
        principal: Annotated[RunnerPrincipal, Depends(require_runner_principal)],
        tenant: Annotated[TenantRequest, Depends(resolve_tenant)],
    ) -> FleetRequest:
        return cls(principal, request.app.state.config, tenant.scope, tenant.name, tenant.services)

    @classmethod
    def of_even_if_retired(
        cls,
        request: Request,
        principal: Annotated[RunnerPrincipal, Depends(require_runner_principal_even_if_retired)],
        tenant: Annotated[TenantRequest, Depends(resolve_tenant)],
    ) -> FleetRequest:
        return cls(principal, request.app.state.config, tenant.scope, tenant.name, tenant.services)

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
    services: TenantServices, chunk: Chunk, graph: Graph, completion: Completion, *, follow_latest_default: bool
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
def peek_queue(services: Services, project: QueryProject) -> QueuePeekResponse:
    """The runner's FILL read — the whole ready-queue order: a filling runner needs every
    ready chunk in one read. Kept as-is for a previous-minor caller;
    ``POST /queue/peek`` below is the matched counterpart. ``?project=`` narrows it to that
    project's chunks at their whole-queue positions, never reranked."""
    statuses = services.chunks.facts.load_live_statuses()
    return queue_api.ReadyQueue.of(services, statuses, project=project).view


@router.post("/queue/peek", response_model=QueuePeekResponse)
def peek_matched_queue(
    request: QueuePeekRequest,
    services: Services,
    principal: Annotated[RunnerPrincipal, Depends(require_runner_principal)],
) -> QueuePeekResponse:
    """The matched fleet peek — at most one ready entry of a project the calling principal
    serves that it can both work (its declared capabilities admit the entry) and claim (not
    dependency-blocked), with the request's match policy applied to both. The served set is
    the principal's stored declaration less what its host's report withholds (a binding whose workspace the host has
    not reported, or every binding under an unrostered subscription), narrowed to ``project`` or
    ``projects`` when sent; a runner serving nothing is offered nothing."""
    registration = services.registry.get_runner(principal.runner_id)
    host = None
    if registration is not None and registration.host_id is not None:
        host = services.hosts.get_host(registration.host_id)
    served = (
        frozenset()
        if registration is None
        else offered_projects(registration, host, project=request.project, projects=request.projects)
    )
    statuses = services.chunks.facts.load_live_statuses()
    return queue_api.MatchedPeek.of(services, statuses, request, served=served).view


@router.get("/system-artifacts", response_model=list[SystemArtifactView])
def list_system_artifacts_route(services: Services) -> list[SystemArtifactView]:
    """The full ``system``-scoped artifact set — resolved at call time off the packaged
    set, pinned to no chunk or lease."""
    return system_artifacts_api.list_system_artifacts(services.system_artifacts)


@router.get("/system-artifacts/{name:path}", response_model=SystemArtifactView)
def get_system_artifact_route(name: str, services: Services) -> SystemArtifactView:
    """One published system artifact by its slash-bearing name; ``404`` unknown."""
    return system_artifacts_api.get_system_artifact(name, services.system_artifacts)


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(chunk_id: str, services: Services) -> ChunkDetail:
    """The runner's chunk-status poll — the same aggregate as ``GET /api/chunks/{chunk_id}``."""
    return chunks_api.get_chunk(chunk_id, services)


@router.get("/chunk-statuses", response_model=list[ChunkStatusView])
def get_chunk_statuses(chunk_id: Annotated[list[str], Query()], services: Services) -> list[ChunkStatusView]:
    """The runner tick's slim batch status read — repeatable ``chunk_id``;
    an unknown or ephemeral id is omitted, never a 404."""
    return chunk_statuses_api.chunk_statuses(chunk_id, services)


@router.get("/chunks/{chunk_id}/work-items", response_model=WorkItemsView)
def get_work_items(chunk_id: str, services: Services) -> WorkItemsView:
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
def pause_chunk(chunk_id: str, services: Services) -> ChunkSummary:
    """Pause the chunk with a runner's own bearer token — the same transition as the
    operator route, ``by`` defaulting to ``operator``."""
    return chunks_api.pause_chunk(chunk_id, ChunkPauseRequest(), services)


@router.post("/chunks/{chunk_id}/resume", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def resume_chunk(chunk_id: str, services: Services) -> ChunkSummary:
    """Resume the chunk with a runner's own bearer token. Takes no body, so the
    resume is always recorded as ``operator``."""
    return chunks_api.resume_chunk(chunk_id, ChunkPauseRequest(), services)


@router.get("/summary", response_model=FleetSummaryView)
def fleet_summary(services: Services) -> FleetSummaryView:
    """The fleet-pulse counts, read with a runner's own bearer token. Fleet-router-only:
    this read has no anonymous counterpart."""
    return chunks_api.FleetPulse(services).view()


@router.get("/questions/{question_id}", response_model=QuestionView)
def get_question(question_id: str, services: Services) -> QuestionView:
    """The runner's answer poll before it resumes the dormant session."""
    row = services.chunks.questions.get_question(question_id)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown question {question_id}")
    labels = ProjectLabels.read(services, [row.project_id])
    facts = ChunkFacts.or_default(services.chunks.facts.load_facts(row.chunk_id))
    return questions_api.question_view(
        row,
        services.registry.names_for([row.runner_id]),
        labels.fields(row.project_id),
        superseded_by_restart=facts.restarted_past(row.epoch),
    )


# Moved wholesale — no anonymous caller ever reached these.


@router.get("/chunks/{chunk_id}/envelope", response_model=NodeEnvelope)
def get_envelope(chunk_id: str, services: Services) -> NodeEnvelope:
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


def _routine_run_or_404(chunk_id: str, services: TenantServices) -> RunContext:
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
def get_garden_findings(chunk_id: str, services: Services) -> list[FindingView]:
    """A worker's finding bucket — exactly the findings its delivery may cite; the
    chunk's own run context derives the routine and scope, no caller flag can name another.
    404 for an unknown chunk or one with no run context (not a routine run)."""
    run = _routine_run_or_404(chunk_id, services)
    citable = services.finding_bucket.for_run(run).citable
    labels = ProjectLabels.read(services, (f.project_id for f in citable))
    return [finding_view(f, labels.fields(f.project_id)) for f in citable]


@router.get("/chunks/{chunk_id}/garden/proposals", response_model=list[GardenProposalView])
def get_garden_proposals(
    chunk_id: str,
    services: Services,
    state: Annotated[RoutineProposalState, Query()] = RoutineProposalState.OPEN,
) -> list[GardenProposalView]:
    """A worker's own routine's garden proposals, filtered by `state` (`open` by
    default, plus `closed` and `all`, each closed entry carrying its closure) — the
    chunk's own run context derives the routine; no caller-supplied flag can name
    another, and no scope filter applies (a proposal carries no scope column). 404 both
    for an unknown chunk and for one carrying no run context (not a routine run): a
    chunk with nothing to read is refused rather than answered with an empty bucket.

    Unpaged, kept for a previous-minor runner that reads the whole docket in one request;
    `/garden/proposals/page` is the bounded form current runners follow."""
    run = _routine_run_or_404(chunk_id, services)
    labels = ProjectLabels.read(services, [run.project_id]).fields(run.project_id)
    return [
        garden_proposal_view(p, closure, labels)
        for p, closure in services.routine_garden_proposals.list_for_routine(run.project_id, run.routine_name, state)
    ]


@router.get("/chunks/{chunk_id}/garden/proposals/page", response_model=GardenProposalsPageView)
def get_garden_proposals_page(
    chunk_id: str,
    services: Services,
    state: Annotated[RoutineProposalState, Query()] = RoutineProposalState.OPEN,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> GardenProposalsPageView:
    """One keyset page of `get_garden_proposals`'s docket, newest first — the same filters and
    404s, `limit` bounding what the request reads however long the routine's history; follow
    `next_cursor` until it is `None` for the whole docket. 422 on a cursor this route did not issue."""
    run = _routine_run_or_404(chunk_id, services)
    labels = ProjectLabels.read(services, [run.project_id]).fields(run.project_id)
    try:
        pairs, next_cursor = services.routine_garden_proposals.list_page_for_routine(
            run.project_id, run.routine_name, state=state, cursor=cursor, limit=limit
        )
    except MalformedCursor as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="malformed cursor") from exc
    return GardenProposalsPageView(
        proposals=[garden_proposal_view(p, closure, labels) for p, closure in pairs], next_cursor=next_cursor
    )


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
    services: Services,
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
    services: Services,
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
) -> AnalyticsCountsResponse:
    """A worker's own routine-run read of ``GET /api/analytics/counts/skills``."""
    _routine_run_or_404(chunk_id, services)
    return counts_response(services.analytics_events.counts_by_skill(window.event_scope.criteria))


@router.get("/chunks/{chunk_id}/analytics/counts/agent-types", response_model=AnalyticsCountsResponse)
def get_chunk_analytics_counts_agent_types(
    chunk_id: str,
    services: Services,
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
) -> AnalyticsCountsResponse:
    """A worker's own routine-run read of ``GET /api/analytics/counts/agent-types``."""
    _routine_run_or_404(chunk_id, services)
    return counts_response(services.analytics_events.counts_by_agent_type(window.event_scope.criteria))


@router.get("/chunks/{chunk_id}/analytics/counts/nodes", response_model=AnalyticsCountsResponse)
def get_chunk_analytics_counts_nodes(
    chunk_id: str,
    services: Services,
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
    by_name: Annotated[bool, Query()] = False,
) -> AnalyticsCountsResponse:
    """A worker's own routine-run read of ``GET /api/analytics/counts/nodes``."""
    _routine_run_or_404(chunk_id, services)
    return counts_response(named_counts(services.analytics_events.counts_by_node(window.event_scope.criteria), by_name))


@router.get("/chunks/{chunk_id}/analytics/spend/nodes", response_model=AnalyticsSpendResponse)
def get_chunk_analytics_spend_nodes(
    chunk_id: str,
    services: Services,
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
    services: Services,
    window: Annotated[AnalyticsWindow, Depends(AnalyticsWindow.of)],
    by_name: Annotated[bool, Query()] = False,
) -> AnalyticsSpendResponse:
    """A worker's own routine-run read of ``GET /api/analytics/spend/graphs``."""
    _routine_run_or_404(chunk_id, services)
    stats = services.operational_analytics.spend_by_graph(operational_criteria(window.scope))
    return spend_response(named_spend(stats, by_name))


@router.get("/scopes", response_model=list[ScopeView])
def get_scopes(services: Services, chunk_id: Annotated[str | None, Query()] = None) -> list[ScopeView]:
    """Every scope, newest first, each marked retired or not — the
    deployment's scope vocabulary; with `chunk_id`, only the scopes of that chunk's project, the vocabulary a
    worker on that chunk may name. 404 on an unknown chunk."""
    project_id = None
    if chunk_id is not None:
        chunk = services.chunks.record.get(chunk_id)
        if chunk is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
        project_id = chunk.require_project()
    scopes = services.scopes.list_all(project_id)
    labels = ProjectLabels.read(services, (s.project_id for s in scopes))
    return [scope_view(s, labels.fields(s.project_id)) for s in scopes]


def _answered_findings_or_404(chunk_id: str, services: TenantServices) -> list[FindingView]:
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    findings = services.answered_findings.resolve_for_chunk(chunk)
    if findings is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"chunk {chunk_id} answers no accepted, minted garden proposal",
        )
    labels = ProjectLabels.read(services, (f.project_id for f in findings))
    return [finding_view(f, labels.fields(f.project_id)) for f in findings]


@router.get("/chunks/{chunk_id}/findings", response_model=list[FindingView])
def get_chunk_findings(chunk_id: str, services: Services) -> list[FindingView]:
    """The findings the chunk's own accepted, minted garden proposal answers — a worker's
    per-chunk read, distinct from ``get_garden_findings``'s routine-run bucket. 404 both
    for an unknown chunk and for one answering no such proposal."""
    return _answered_findings_or_404(chunk_id, services)


@router.get("/chunks/{chunk_id}/findings/{finding_id}", response_model=FindingView)
def get_chunk_finding(chunk_id: str, finding_id: str, services: Services) -> FindingView:
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
    services: Services,
) -> HubAdvanceResponse:
    """Drive a chunk parked at a generic hub command node one step, taking one pass of that
    node's action under the fleet-wide serialization slot. ``ran=False`` is never an
    error: another chunk holds the slot, the node is ``pending`` inside ``poll_interval``, the
    chunk is escalated, or it is not parked at a drivable hub node — ``detail`` names which."""
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
        next_poll_at = PollPolicy.of(node).due_at(pending) if pending is not None else None
        # A future `next_poll_at` distinguishes "not yet due to poll" from a genuinely busy slot;
        # a pending node whose interval elapsed but lost the slot race falls through to the busy branch.
        if facts.open_escalation() is not None:
            detail = "escalated — awaiting a person"
        elif next_poll_at is not None and next_poll_at > services.clock.now():
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


def _incompatible_denial(
    services: TenantServices, chunk_id: str, exc: ClaimDeniedIncompatible
) -> RouteClaimIncompatibleDenial:
    """The incompatible-claim body: a capability refusal keeps its standing ``detail``; a project refusal's
    ``detail`` names the chunk's project by its slug as it stands."""
    if exc.reason is not ClaimDenialReason.PROJECT or exc.project_id is None:
        return RouteClaimIncompatibleDenial(chunk_id=chunk_id, incompatible_runner_id=exc.runner_id)
    project = services.projects.get(ProjectId(exc.project_id))
    named = exc.project_id if project is None else project.slug
    return RouteClaimIncompatibleDenial(
        chunk_id=chunk_id,
        incompatible_runner_id=exc.runner_id,
        reason=ClaimDenialReason.PROJECT,
        detail=f"runner does not serve the chunk's project {named}",
    )


@claim_router.post("/routes", response_model=RouteClaimResponse, status_code=status.HTTP_201_CREATED)
def claim_route(
    claim: RouteClaim,
    services: Services,
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of_even_if_retired)],
) -> object:
    """Claim a chunk; 403 if the runner is unregistered, retired, refused on version, or paused at the hub, 409 if
    already claimed, already terminal ({done, stopped}), not ready, standing on an unmet prerequisite, of a project
    the runner does not serve, or incompatible with the runner's stored capabilities, else the first node
    envelope."""
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
    except ClaimDeniedVersionRefused as exc:
        version_refused = RouteClaimPausedDenial(chunk_id=claim.chunk_id, runner_id=exc.runner_id, detail=str(exc))
        return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=version_refused.model_dump())
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
        incompatible_denial = _incompatible_denial(services, claim.chunk_id, exc)
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
    services: Services,
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
    try:
        route_token = services.claim.rekey(route)
    except RekeyDeniedNotHolder as exc:
        # The route moved after the pre-check: answer as the pre-check would have.
        if exc.holder is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        fleet.assert_owns(exc.holder)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except RekeyDeniedTerminal as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RouteTokenRekeyResponse(chunk_id=chunk_id, route_token=route_token)


_RELEASE_CONFLICT_REASONS = {
    ReleaseRefusalKind.STALE: ReleaseRefusalReason.STALE,
    ReleaseRefusalKind.NOT_RUNNING: ReleaseRefusalReason.NOT_RUNNING,
}


@router.post(
    "/chunks/{chunk_id}/release",
    response_model=ReleaseResponse,
    responses={status.HTTP_409_CONFLICT: {"model": ReleaseRefusal}},
)
def release_chunk(
    chunk_id: str,
    request: ReleaseRequest,
    services: Services,
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> object:
    """Give the chunk's route back at its holder's own request, so it re-derives ``ready`` at its
    current node: 200 with the id of the release it recorded; 409 when the chunk moved past
    ``epoch`` or is not running; 403 when the caller does not hold the route or its route token
    does not match; 404 for a chunk the tenant does not hold."""
    runner_id = fleet.caller_id()
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        released_id = services.release.release(
            chunk,
            runner_id=runner_id,
            epoch=request.epoch,
            route_token=request.route_token,
            reason=request.reason,
            route_token_mode=fleet.route_token_mode,
        )
    except ReleaseRefused as exc:
        reason = _RELEASE_CONFLICT_REASONS.get(exc.kind)
        if reason is None:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        refusal = ReleaseRefusal(chunk_id=chunk_id, reason=reason, detail=str(exc))
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=refusal.model_dump(mode="json"))
    change.publish(cause="released", key=f"route_released:{released_id}", released_by=runner_id, reason=request.reason)
    services.events.publish_queue_changed()  # a released chunk re-enters the ready queue
    return ReleaseResponse(chunk_id=chunk_id, released_id=released_id)


@router.post("/chunks/{chunk_id}/completions", response_model=ApplyResponse)
def submit_completion(
    chunk_id: str,
    submission: CompletionSubmission,
    services: Services,
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
    services: Services,
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
    services: Services,
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
    services: Services,
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
    services: Services,
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


@router.post(
    "/runners",
    response_model=RunnerRegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    responses={status.HTTP_409_CONFLICT: {"model": RunnerVersionRefusal}},
)
def register_runner(
    request: RunnerRegistrationRequest,
    services: Services,
    fleet: Annotated[FleetRequest, Depends(FleetRequest.of)],
) -> object:
    """Register the calling runner — the one its bearer token names — with its workspace binding
    and the name it declares; idempotent.

    The hub never rejects a registration over its roster — it doubles as the heartbeat every tick,
    so the tenant it answers, named as the hub holds it now, carries a rename on the next tick. A
    body ``runner_id`` from an older runner is ignored. A retired runner is refused 403, a refused version 409."""
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
    try:
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
            projects=request.projects,
            bindings=(
                None
                if request.bindings is None
                else tuple(RunnerBinding(b.project, b.workspace_id, b.agents) for b in request.bindings)
            ),
            subscription=request.subscription,
            agents=request.agents,
            declining=(
                None
                if request.declining is None
                else tuple(
                    RunnerDeclining(d.lever, d.detail, d.subscription, None if d.until is None else as_utc(d.until))
                    for d in request.declining
                )
            ),
            version=request.version,
        )
    except RunnerVersionRefused as exc:
        if exc.changed:
            services.events.publish_runner_changed(runner_id, kind="registered", runner_name=registration.name)
        refusal = RunnerVersionRefusal(
            reason=exc.refusal.reason,
            runner_version=exc.judgement.reported_version,
            hub_version=exc.judgement.hub_version,
            floor=exc.judgement.floor,
            detail=exc.refusal.detail,
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=refusal.model_dump(mode="json"))
    if registered.changed:
        services.events.publish_runner_changed(runner_id, kind="registered", runner_name=registered.name)
    hosts = runners_api.hosts_of(services, [registration])
    host = None if registration.host_id is None else hosts.get(registration.host_id)
    flags = flag_bindings(registered.bindings, registered.subscription, host)
    return RunnerRegistrationResponse(
        runner_id=runner_id,
        runner_name=registered.name,
        first_registration=registered.first,
        tenant_id=fleet.scope.tenant,
        tenant_name=fleet.tenant_name,
        projects=runners_api.served_views(
            registered.projects, runners_api.served_projects(services, registered.projects)
        ),
        bindings=runners_api.binding_views(registered.bindings, flags),
        subscription=registered.subscription,
        subscription_unrostered=flags.subscription_unrostered,
        hub_version=registered.version.hub_version,
        window=VersionWindowView(floor=registered.version.floor, ceiling=registered.version.ceiling),
    )


@router.post("/runners/{runner_id}/heartbeats", status_code=status.HTTP_204_NO_CONTENT)
def heartbeat_runner(
    runner_id: str,
    services: Services,
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
    services: Services,
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
    return runners_api.runner_view(
        services.fleet.own_liveness(registration),
        now=services.clock.now(),
        projects=runners_api.served_projects(services, registration.served),
        hosts=runners_api.hosts_of(services, [registration]),
    )
