"""Chunk routes — the anonymous **operator** surface.

Controllers stay read-only over the store (``bzh:controller-read-only``); list/detail
reads derive status and current node from facts (``bzh:facts-not-status``), never a
stored column. The work-item read is a pass-through whose contents are never stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from blizzard.auth_core import Permission
from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkDeleteRefusal, ChunkStatus
from blizzard.foundation.store.utc import as_utc, iso_utc
from blizzard.foundation.work_items import WorkItemPriority
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.board_window import BOARD_DONE_WINDOW
from blizzard.hub.api.chunk_edit import ChunkPatchBody
from blizzard.hub.api.chunk_views import ChunkView, blocked_view, ingest_conflict_response
from blizzard.hub.api.delivery_declarations import delivery_declarations_for
from blizzard.hub.api.deps import Services
from blizzard.hub.api.flow_places import chunk_flow_places
from blizzard.hub.api.graph_names import GraphNames, graph_by_ref
from blizzard.hub.api.project_label_fields import project_label_fields
from blizzard.hub.api.project_params import QueryProject, body_project, project_of_call, project_ref
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.delivery.flow_changes import publish_flow_facts
from blizzard.hub.delivery.marker_auth import AmbiguousMarkerRepository
from blizzard.hub.domain.chunk.delivery_read import DeliveryRead, DeliverySources
from blizzard.hub.domain.chunk.dependencies import (
    ChunkNeighbor,
    derive_blocked_prerequisites,
    derive_chunk_neighborhood,
)
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.ingest import (
    EmptyIngest,
    IngestConflict,
    IngestItemProjectMismatch,
    IngestProjectAmbiguous,
    IngestProjectUnknown,
    IngestSourceNotLinked,
    ingest_work_refs,
)
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, FleetSummary, WorkRef
from blizzard.hub.domain.execution.decisions import NotEscalated
from blizzard.hub.domain.execution.detach import NotRouted
from blizzard.hub.domain.garden.delivery.materialize import DeliveryOutcome
from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected
from blizzard.hub.domain.garden.review.materialize import ReviewFindingsOutcome
from blizzard.hub.domain.garden.review.validation import ReviewFindingsRejected
from blizzard.hub.domain.graph.authoring import DefaultGraphRetired
from blizzard.hub.domain.graph.model import TargetGraphRetired
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.kernel.pagination import DEFAULT_LIMIT, MAX_LIMIT, MalformedCursor
from blizzard.hub.domain.operations.delete import (
    ChunkHasDependents,
    ChunkNotDeletable,
    delete_refusal,
    dependent_chunk_ids,
)
from blizzard.hub.domain.operations.demote import ChunkNotDemotable
from blizzard.hub.domain.operations.edit import (
    ChunkAlreadyMoved,
    ChunkNotEditable,
    ForcedNodeUnknown,
    MigrationTargetIsCurrentPin,
)
from blizzard.hub.domain.operations.pause import ChunkNotPausable
from blizzard.hub.domain.operations.promote import ChunkNotPromotable
from blizzard.hub.domain.operations.queue import ContradictoryPlacement, NotInList, Placement, SelfAnchoredMove
from blizzard.hub.domain.operations.restart import (
    ChunkNotRestartable,
    RestartCurrentNodeUnknown,
    RestartGraphPinChanged,
    RestartNodeUnknown,
)
from blizzard.hub.domain.operations.stop import ChunkNotStoppable
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.work_sources.source import AuthorView, WorkSourceError
from blizzard.wire.chunk import (
    BlockedView,
    ChunkCompleteRequest,
    ChunkCountsView,
    ChunkDeleteRequest,
    ChunkDeleteResponse,
    ChunkDetail,
    ChunkIngestRequest,
    ChunkIngestResponse,
    ChunkNeighborhoodView,
    ChunkNeighborView,
    ChunkPatchRequest,
    ChunkPatchResponse,
    ChunkPauseRequest,
    ChunkPlacementRequest,
    ChunkRequeueRequest,
    ChunkRestartRequest,
    ChunksPageView,
    ChunkStopRequest,
    ChunkSummary,
    GardenDeliveryRequest,
    GardenDeliveryResponse,
    HubMarkerRequest,
    HubMarkerResponse,
    ReviewFindingsDeliveryResponse,
    WorkItemEntry,
    WorkItemsView,
)
from blizzard.wire.fleet import FleetSummaryView
from blizzard.wire.project import ProjectRequiredBody, SourceNotLinkedBody
from blizzard.wire.work_source import WorkItemAuthorView

#: How many of the most recently finished ``done`` chunks stay on it however old, per tenant (per project
#: under ``?project=``) — the board's Done lane is never emptier than this while that many exist.
BOARD_DONE_FLOOR = 10

router = APIRouter(
    prefix="/api", tags=["chunks"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)

#: A delivery the write fence refused — a 409, never ``invalid``, which would write a failure marker.
_FENCED_DELIVERY_DETAIL = "delivery superseded: the chunk is terminal or was restarted"


@dataclass(frozen=True)
class OpenDecision:
    """A chunk's graph gate."""

    services: TenantServices
    chunk_id: str

    def publish(self) -> None:
        """Emit ``decision-opened`` if the chunk now carries a live, unresolved gate."""
        decision = self.services.chunks.decisions.decision_for_chunk(self.chunk_id)
        if decision is not None and decision.is_open:
            chunk = self.services.chunks.record.get(self.chunk_id)
            self.services.events.publish_decision_opened(
                self.chunk_id,
                decision.decision_id,
                key=f"decisions:{decision.decision_id}",
                project_id=chunk.project_id if chunk is not None else None,
            )


def _ingest_project(services: TenantServices, pointers: list[WorkRef], named: ProjectId | None) -> ProjectId:
    """The project an ingest of ``pointers`` mints into: ``named``, or the one its configured sources agree on,
    or the one its hub items record. When nothing it holds names one, the call acts in the tenant's only
    project, :class:`ProjectRequired` otherwise."""
    try:
        return services.ingest.choose_project(pointers, named)
    except IngestProjectUnknown:
        return project_of_call(services).project_id


def ingest_project_refusal(
    services: TenantServices, exc: IngestProjectAmbiguous | IngestSourceNotLinked
) -> JSONResponse:
    """An ingest's project refusal as its ``422`` body: ``source_not_linked`` naming the source and
    the project it is not linked to (``null`` when it is linked to none); otherwise
    ``project_required`` listing the candidates the tokens left open."""
    if isinstance(exc, IngestSourceNotLinked):
        project = services.projects.get(exc.project) if exc.project is not None else None
        body: BaseModel = SourceNotLinkedBody(
            message=str(exc),
            source=exc.source,
            slug=services.work_sources.source_label(exc.source),
            project=project_ref(project) if project is not None else None,
        )
    else:
        found = services.projects.get_many(list(exc.candidates))
        projects = [found[project_id] for project_id in exc.candidates if project_id in found]
        body = ProjectRequiredBody(message=str(exc), projects=[project_ref(project) for project in projects])
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, content={"detail": body.model_dump()})


@router.post(
    "/chunks",
    response_model=ChunkIngestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.CHUNK_INGEST))],
)
def ingest_chunk(request: ChunkIngestRequest, services: Services) -> object:
    """Ingest by source-native token into the project named, or the one the tokens' sources
    agree on; 422 on a project the tenant does not hold, a token no configured source
    claims, a project the tokens leave open (``project_required``), a source the
    project is not linked to (``source_not_linked``) and a hub item recorded in another
    project; 409 on a pointer held by a live chunk;
    503 if every graph named after the packaged default has been retired (the operator's
    brake, not a code bug: re-enable one or mint a new one)."""
    # Resolution before minting, and before the live-holder check: an unresolvable
    # token should not consult the store, and the request rejects as a whole.
    named = body_project(request.project, services).project_id if request.project is not None else None
    pointers: list[WorkRef] = []
    for token in request.tokens:
        pointer = services.work_sources.resolve(token)
        if pointer is None:
            configured = ", ".join(sorted(services.work_sources.names())) or "none"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(f"token {token!r} is not claimed by any configured work source (configured: {configured})"),
            )
        pointers.append(pointer)
    try:
        # The batch is refused before the default graph resolves, which may mint one.
        pointers = ingest_work_refs(pointers)
        project = _ingest_project(services, pointers, named)
    except EmptyIngest as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except (IngestProjectAmbiguous, IngestSourceNotLinked) as exc:
        return ingest_project_refusal(services, exc)
    except IngestItemProjectMismatch as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    try:
        graph = services.graph_mint.ensure_default(
            services.default_graph_doc, definition_yaml=services.default_graph_yaml
        )
    except DefaultGraphRetired as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    try:
        chunk_id = services.ingest.ingest(pointers, graph=graph, project=project)
    except IngestSourceNotLinked as exc:  # unlinked after the project was chosen
        return ingest_project_refusal(services, exc)
    except IngestConflict as exc:
        return ingest_conflict_response(services, exc)
    chunk_events.ChunkChanged.of(services, chunk_id, prev_status=None).publish(cause="minted", key=f"chunks:{chunk_id}")
    services.events.publish_queue_changed()  # mint adds the chunk to the backlog list
    return ChunkIngestResponse(chunk_id=chunk_id, **project_label_fields(project, services.projects.get(project)))


@router.get("/chunks", response_model=ChunksPageView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_chunks(
    services: Services,
    project: QueryProject,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    board_window: Annotated[bool, Query()] = False,
) -> ChunksPageView:
    """The fleet chunk list — derived status per chunk, bounded and keyset-paginated;
    ``?project=`` narrows the rows to that project's chunks, in the same order.

    Reads only what the page renders: facts, routes, blocked markings, and live holders are
    each resolved for the page's chunks and pointers through narrowed, batched reads. A pointer
    the page renders held live by a chunk outside it, or a dependent's prerequisite outside it,
    still resolves — the narrowing happens in the store, not by dropping the off-page side."""
    # ``board_window`` drops ``done`` chunks finished before the window, bar the floor of newest ones; a page
    # can come back short. The floor is one extra statement returning at most ``BOARD_DONE_FLOOR`` ids, ranking
    # the scope's done chunks by a derived instant no index orders — a fleet-sized walk by design.
    done_since = services.clock.now() - BOARD_DONE_WINDOW if board_window else None
    done_floor = services.chunks.record.newest_done(limit=BOARD_DONE_FLOOR, project=project) if board_window else []
    names = GraphNames(services.graphs)
    try:
        page = services.chunks.record.list_page(
            cursor=cursor, limit=limit, done_since=done_since, done_kept=done_floor, project=project
        )
    except MalformedCursor as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="malformed cursor") from exc
    facts = services.chunks.facts.load_facts_for([c.chunk_id for c in page.chunks])
    if done_since is not None:
        # The store's exclusion leaves a same-instant movement tie in; the derivation settles it. A floor
        # member is exempt: it stays however old.
        kept = set(done_floor)
        page_chunks = [
            c
            for c in page.chunks
            if c.chunk_id in kept or not ChunkFacts.or_default(facts.get(c.chunk_id)).finished_before(done_since)
        ]
    else:
        page_chunks = page.chunks
    page_ids = [c.chunk_id for c in page_chunks]
    routes = services.chunks.route.routes_for(page_ids)
    # The dependency edges join the facts at this call site, not inside a store
    # (``bzh:dependency-inversion``): the page dependents' standing edges, with their
    # prerequisites' statuses read by id when the prerequisite is off the page.
    edges = services.chunks.dependencies.standing_edges_for_dependents(page_ids)
    statuses = {chunk_id: chunk_facts.status() for chunk_id, chunk_facts in facts.items()}
    off_page = sorted({e.prerequisite_chunk_id for e in edges if e.prerequisite_chunk_id not in statuses})
    if off_page:
        statuses.update(
            (chunk_id, chunk_facts.status())
            for chunk_id, chunk_facts in services.chunks.facts.status_facts_for(off_page).items()
        )
    markings = derive_blocked_prerequisites(edges, statuses)
    # One priming call resolves the page's own pinned graphs' name/entry-node/node-names
    # up front — narrowed to the page, since nothing outside it is rendered.
    names.prime(chunk.graph_id for chunk in page_chunks)
    # Unlike the fleet-wide status/holder reads above, delivery belongs only to
    # rendered rows: one narrowed, batched read over this page's ids.
    delivery_sources = services.chunks.artifacts.delivery_sources_for([chunk.chunk_id for chunk in page_chunks])
    declarations = delivery_declarations_for(services, page_chunks, delivery_sources)
    # A pointer the page renders can be held live by a chunk outside it; the store narrows to live candidates.
    live_holders = services.chunks.work_refs.live_holders(p for chunk in page_chunks for p in chunk.work_refs)
    # The page's routed runners' registered names — one batched read, narrowed to the page.
    runner_names = services.registry.names_for(
        route.runner_id for chunk in page_chunks if (route := routes.get(chunk.chunk_id)) is not None
    )
    # The page's projects — one batched read, narrowed to the page.
    projects = services.projects.get_many(sorted({c.project_id for c in page_chunks if c.project_id is not None}))
    # Config revisions are read once per distinct source on each request, never cached across requests.
    sources = {
        name: services.work_sources.get(name)
        for name in sorted({pointer.source for chunk in page_chunks for pointer in chunk.work_refs})
    }
    # A page chunk's flow place depends on the submissions ahead of it, whose chunks need not be on the page: the
    # read covers the whole line of every flow the page's chunks hold — bounded by the line, not the fleet.
    flow_places = chunk_flow_places(services, page_ids)
    return ChunksPageView(
        chunks=[
            ChunkView.injected(
                services,
                chunk,
                facts.get(chunk.chunk_id) or ChunkFacts(minted=True),
                routes.get(chunk.chunk_id),
                names,
                live_holders,
                blocked=blocked_view(markings.get(chunk.chunk_id)),
                delivery=DeliveryRead.of(
                    facts.get(chunk.chunk_id) or ChunkFacts(minted=True),
                    delivery_sources.get(chunk.chunk_id, DeliverySources()),
                    declarations.get(chunk.chunk_id, []),
                ),
                runner_names=runner_names,
                projects=projects,
                sources=sources,
                flow_places=flow_places,
            ).summary()
            for chunk in page_chunks
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/chunk-counts", response_model=ChunkCountsView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def chunk_counts(
    services: Services, project: QueryProject, done_since: Annotated[datetime | None, Query()] = None
) -> ChunkCountsView:
    """The all-time fleet count per derived status — over exactly the chunks
    ``GET /api/chunks`` pages over, with no window applied; ``?project=`` counts that
    project's chunks alone. With ``done_since``, ``done_since`` answers how many of the ``done`` ones finished at or
    after that instant; the caller chooses the boundary."""
    counts = services.chunks.facts.status_counts(project=project)
    return ChunkCountsView(
        done_since=(
            services.chunks.facts.done_since_count(as_utc(done_since), project=project)
            if done_since is not None
            else None
        ),
        total=sum(counts.values()),
        terminal=sum(n for st, n in counts.items() if st in TERMINAL_STATUSES),
        **{st.value: n for st, n in counts.items()},
    )


def _neighbor_view(neighbor: ChunkNeighbor) -> ChunkNeighborView:
    return ChunkNeighborView(chunk_id=neighbor.chunk_id, status=neighbor.status, satisfied=neighbor.satisfied)


def _dependency_views_for_chunk(
    services: TenantServices, chunk_id: str, *, facts: ChunkFacts
) -> tuple[BlockedView | None, ChunkNeighborhoodView, ChunkDeleteRefusal | None]:
    """``GET /api/chunks/{chunk_id}``'s blocked marking, neighborhood and delete refusal — all derived
    from the one standing-edges read and one statuses map, since a chunk's dependent edges are a subset of its own
    neighborhood edges and reading them separately would risk a release or completion landing between the two reads.
    ``facts`` is the caller's own already-loaded value for ``chunk_id``, so this need not reload them a second
    time."""
    edges = services.chunks.dependencies.standing_edges_for(chunk_id)
    refusal = delete_refusal(facts, dependent_chunk_ids(chunk_id, edges))
    if not edges:
        return None, ChunkNeighborhoodView(prerequisites=[], dependents=[]), refusal
    statuses: dict[str, ChunkStatus] = {chunk_id: facts.status()}
    neighbor_ids = {
        edge.prerequisite_chunk_id if edge.dependent_chunk_id == chunk_id else edge.dependent_chunk_id for edge in edges
    }
    neighbor_facts_by_id = services.chunks.facts.load_facts_for(list(neighbor_ids))
    statuses.update((neighbor_id, nf.status()) for neighbor_id, nf in neighbor_facts_by_id.items())
    dependent_edges = [e for e in edges if e.dependent_chunk_id == chunk_id]
    blocked = blocked_view(derive_blocked_prerequisites(dependent_edges, statuses).get(chunk_id))
    neighborhood = derive_chunk_neighborhood(chunk_id, edges, statuses)
    return (
        blocked,
        ChunkNeighborhoodView(
            prerequisites=[_neighbor_view(n) for n in neighborhood.prerequisites],
            dependents=[_neighbor_view(n) for n in neighborhood.dependents],
        ),
        refusal,
    )


@dataclass(frozen=True)
class FleetPulse:
    """Every chunk's derived status folded to the four fleet-summary counts."""

    services: TenantServices

    def view(self) -> FleetSummaryView:
        """Not a route of its own here. Derives each chunk's status the same way
        :func:`list_chunks` does, but yields only the four bucket integers, so the payload
        is a fixed four numbers regardless of fleet size. Reads only the live fleet's
        statuses (``bzh:live-set-read``) with one bulk read rather than fanning
        ``load_facts`` out per chunk — a terminal chunk counts toward no bucket."""
        summary = FleetSummary.of(self.services.chunks.facts.load_live_statuses().values())
        return FleetSummaryView(
            ready=summary.ready,
            running=summary.running,
            waiting=summary.waiting,
            needs=summary.needs,
        )


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def get_chunk(chunk_id: str, services: Services) -> ChunkDetail:
    """One chunk aggregate in full — derived status, current node, route."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    facts = services.chunks.facts.load_facts(chunk_id) or ChunkFacts(minted=True)
    blocked, neighborhood, refusal = _dependency_views_for_chunk(services, chunk_id, facts=facts)
    # Primed with every graph id this chunk's history ever names.
    names = GraphNames(services.graphs)
    names.prime(_detail_graph_ids(chunk, facts))
    return ChunkView.of(
        services, chunk, names=names, blocked=blocked, facts=facts, neighborhood=neighborhood, delete_refusal=refusal
    ).detail()


def _detail_graph_ids(chunk: Chunk, facts: ChunkFacts) -> set[str]:
    """Every graph id ``GraphNames`` must prime for one chunk detail read: the current
    pin, every transition's/restart's/migration's own graph, and the intended
    migration's target — the full set the detail render's history walks against."""
    graph_ids = {chunk.graph_id}
    graph_ids.update(t.graph_id for t in facts.transitions if t.graph_id is not None)
    for r in facts.restarts:
        graph_ids.add(r.graph_id)
        if r.from_graph_id is not None:
            graph_ids.add(r.from_graph_id)
    for m in facts.migrations:
        graph_ids.add(m.from_graph_id)
        graph_ids.add(m.to_graph_id)
    if chunk.intended_migration is not None:
        graph_ids.add(chunk.intended_migration.graph_id)
    return graph_ids


@router.post(
    "/chunks/{chunk_id}/hub-markers",
    response_model=HubMarkerResponse,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
    deprecated=True,
)
def record_hub_marker(
    chunk_id: str,
    node_id: str,
    epoch: int,
    request_body: HubMarkerRequest,
    services: Services,
) -> HubMarkerResponse:
    """Record a marker artifact on a hub node's visit, for a person holding ``chunk:control``. Deprecated:
    a hub node's action records its own markers. Idempotent per ``(chunk, node, name, epoch)``."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    node = graph.node_by_id(node_id) if graph is not None else None
    node_name = node.name if node is not None else node_id
    try:
        recorded = services.hub_markers.record_marker(
            chunk_id,
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            name=request_body.name,
            content=request_body.content,
        )
    except AmbiguousMarkerRepository as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return HubMarkerResponse(recorded=recorded, chunk_id=chunk_id, name=request_body.name)


@router.post(
    "/chunks/{chunk_id}/garden-delivery",
    response_model=GardenDeliveryResponse,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
    deprecated=True,
)
def record_garden_delivery(
    chunk_id: str,
    node_id: str,
    epoch: int,
    request_body: GardenDeliveryRequest,
    services: Services,
) -> GardenDeliveryResponse:
    """The garden delivery node's own route — validates a delivering node's
    ``--delta``/``--proposals`` artifacts and, on success, materializes them in one
    transaction. An unresolvable run context or a failed validation is an ``invalid``
    outcome at a 200, never an error response — the graph's own ``invalid`` edge reads
    and routes on it. A delivery the chunk has since been stopped or restarted past is a 409."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    node = graph.node_by_id(node_id) if graph is not None else None
    if node is None:
        return GardenDeliveryResponse(outcome="invalid", detail=f"unknown node {node_id!r} for chunk {chunk_id}")

    run = services.run_context.for_chunk(chunk)
    if run is None:
        return GardenDeliveryResponse(outcome="invalid", detail=f"no run context for chunk {chunk_id}")

    try:
        outcome = services.garden_delivery.record(
            chunk=chunk,
            node=node,
            epoch=epoch,
            run=run,
            delta_names=request_body.delta,
            proposal_names=request_body.proposals,
        )
    except GardenDeliveryRejected as exc:
        return GardenDeliveryResponse(outcome="invalid", detail=str(exc))
    # Both recorded `DeliveryOutcome` members mean "durably recorded" to this route's
    # caller — a replay minting nothing is not itself news.
    if outcome is DeliveryOutcome.FENCED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_FENCED_DELIVERY_DETAIL)
    return GardenDeliveryResponse(outcome="recorded", detail="")


@router.post(
    "/chunks/{chunk_id}/review-findings-delivery",
    response_model=ReviewFindingsDeliveryResponse,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
    deprecated=True,
)
def record_review_findings_delivery(
    chunk_id: str,
    node_id: str,
    epoch: int,
    services: Services,
) -> ReviewFindingsDeliveryResponse:
    """The `record-findings` node's own route — validates the chunk's
    newest `review-finding-delta` artifact and, on success, materializes its `deferred`
    entries in one transaction. A malformed delta or an unresolvable node is an
    ``invalid`` outcome at a 200, never an error response; a delivery the chunk has since been
    stopped or restarted past is a 409. Idempotent per chunk."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    node = graph.node_by_id(node_id) if graph is not None else None
    if node is None:
        return ReviewFindingsDeliveryResponse(
            outcome="invalid", detail=f"unknown node {node_id!r} for chunk {chunk_id}"
        )

    try:
        outcome = services.review_findings.record(chunk=chunk, node=node, epoch=epoch)
    except ReviewFindingsRejected as exc:
        return ReviewFindingsDeliveryResponse(outcome="invalid", detail=str(exc))
    if outcome is ReviewFindingsOutcome.FENCED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_FENCED_DELIVERY_DETAIL)
    return ReviewFindingsDeliveryResponse(outcome="recorded", detail="")


@router.post(
    "/chunks/{chunk_id}/requeues",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
def requeue_chunk(
    chunk_id: str,
    services: Services,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
    request: ChunkRequeueRequest | None = None,
) -> ChunkSummary:
    """Close an escalation by supersession: requeue at the current node, recording who requeued and the
    optional body's note."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        requeue_id = services.requeue.requeue(
            chunk, by=identity.username, note=request.note if request is not None else None
        )
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except NotEscalated as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = change.publish(cause="requeued", key=f"requeues:{requeue_id}")
    services.events.publish_queue_changed()  # requeue can re-admit the chunk to the queue
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/restart",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def restart_chunk(chunk_id: str, request: ChunkRestartRequest, services: Services) -> ChunkSummary:
    """Force a chunk onto a node now, on a freshly minted session (#370, #371).

    At a bumped epoch, so the running attempt is fenced out and the holding runner re-enters;
    ``node`` omitted means its current node, or the entry of the graph it lands on when it never
    moved. 409 refuses a terminal chunk, an unmatched name, or its own pin; 404 an unknown target."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    to_graph = graph_by_ref(services.graphs, request.to_graph) if request.to_graph is not None else None
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        restarted = services.restart.restart(
            chunk, graph, node_name=request.node, by=request.by, to_graph=to_graph, note=request.note
        )
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (
        ChunkNotRestartable,
        RestartCurrentNodeUnknown,
        RestartNodeUnknown,
        RestartGraphPinChanged,
        TargetGraphRetired,
        MigrationTargetIsCurrentPin,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = change.publish(cause="restarted", key=f"chunk_restarts:{restarted.fact_id}")
    publish_flow_facts(services.events, restarted.withdrawn)
    services.events.publish_queue_changed()  # an unrouted chunk re-enters the queue at the target node
    # Re-read: a cross-graph move re-pinned the chunk, and the row's node name resolves off that pin.
    return ChunkView.of(services, services.chunks.record.get(chunk_id) or chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/detach",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def detach_chunk(chunk_id: str, services: Services) -> ChunkSummary:
    """Forcibly release a chunk from its runner without touching any escalation."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        released_id = services.detach.detach(chunk)
    except NotRouted as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = change.publish(cause="detached", key=f"route_released:{released_id}")
    services.events.publish_queue_changed()  # a detached chunk re-enters the ready queue
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/pause",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def pause_chunk(chunk_id: str, request: ChunkPauseRequest, services: Services) -> ChunkSummary:
    """Set a chunk's operator pause brake — the claim is kept, unlike detach."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        pause_fact_id = services.pause.pause(chunk, by=request.by)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChunkNotPausable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = change.publish(cause="paused", key=f"chunk_pause_facts:{pause_fact_id}")
    services.events.publish_queue_changed()  # a pause moves the chunk out of the ready queue
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/resume",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def resume_chunk(chunk_id: str, request: ChunkPauseRequest, services: Services) -> ChunkSummary:
    """Clear a chunk's operator pause brake — idempotent, never refused."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    pause_fact_id = services.pause.resume(chunk, by=request.by)
    facts = change.publish(cause="resumed", key=f"chunk_pause_facts:{pause_fact_id}")
    services.events.publish_queue_changed()  # a resume can re-admit the chunk to the queue
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/stop",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def stop_chunk(chunk_id: str, request: ChunkStopRequest, services: Services) -> ChunkSummary:
    """Terminally abandon CHUNK — the operator's last-resort verb.

    Records the ``chunk_stopped`` fact so the chunk derives ``stopped`` and never
    re-derives ``ready``, releases any live route, and supersedes any open escalation. 409
    when the chunk is already ``done`` or ``stopped`` — stopping is not retroactive."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        stopped = services.stop.stop(chunk, by=request.by)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChunkNotStoppable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = change.publish(cause="stopped", key=f"chunk_stopped:{stopped.fact_id}")
    publish_flow_facts(services.events, stopped.withdrawn)
    services.events.publish_queue_changed()  # a stopped chunk is never offered for claim again
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/complete",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def complete_chunk(chunk_id: str, request: ChunkCompleteRequest, services: Services) -> ChunkSummary:
    """Manually complete CHUNK, from any non-``done`` status, including ``stopped``.
    Records the ``chunk_completed`` fact so the chunk derives ``done``, releases any live route,
    closes its flow submissions waiting their turn, and makes the chunk's work refs eligible for closure. Idempotent:
    completing an already-``done`` chunk is a harmless no-op. 404 only when the chunk is
    unknown."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        completed = services.complete.complete(chunk, by=request.by)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    key = f"chunk_completed:{completed.fact_id}" if completed is not None else None
    facts = change.publish(cause="completed", key=key)
    if completed is not None:
        publish_flow_facts(services.events, completed.withdrawn)
    services.events.publish_queue_changed()  # a completed chunk is never offered for claim again
    return ChunkView.of(services, chunk, facts=facts).summary()


def _placement_of(request: ChunkPlacementRequest | None) -> Placement:
    """The :class:`Placement` a promote or demote body names — ``422`` when it names contradictory ones."""
    if request is None:
        return Placement()
    try:
        return Placement(after_chunk_id=request.after_chunk_id, to_top=request.to_top)
    except ContradictoryPlacement as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.post(
    "/chunks/{chunk_id}/promote",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def promote_chunk(chunk_id: str, services: Services, request: ChunkPlacementRequest | None = None) -> ChunkSummary:
    """Promote a not-ready chunk to ready so a runner may claim it.

    It lands at the ready queue's tail unless the optional body names an anchor or the top.
    Idempotent: promoting a currently promoted chunk is a harmless no-op. 404 when the chunk
    is unknown; 409 when a not-currently-promoted chunk is already ``done`` or ``stopped``, or the
    anchor is not in the ready queue; 422 for a self-anchor."""
    placement = _placement_of(request)
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    statuses = services.chunks.facts.load_live_statuses()
    try:
        promoted_id = services.promote.promote(chunk, statuses=statuses, placement=placement)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ChunkNotPromotable, NotInList) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SelfAnchoredMove as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    key = f"chunk_promoted:{promoted_id}" if promoted_id is not None else None
    facts = change.publish(cause="promoted", key=key)
    services.events.publish_queue_changed()  # a promoted chunk enters the ready queue
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/demote",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def demote_chunk(chunk_id: str, services: Services, request: ChunkPlacementRequest | None = None) -> ChunkSummary:
    """Demote a ready chunk back to not-ready, keeping its id, history, edges, pin, and defaults.

    It lands at the backlog's tail unless the optional body names an anchor or the top.
    Idempotent: demoting a chunk that is not currently promoted is a harmless no-op. 404 when the
    chunk is unknown; 409 naming the status when the chunk is claimed, delivering, gated on a human,
    or ended, or the anchor is not in the backlog; 422 for a self-anchor."""
    placement = _placement_of(request)
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    statuses = services.chunks.facts.load_live_statuses()
    try:
        demoted_id = services.demote.demote(chunk, statuses=statuses, placement=placement)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ChunkNotDemotable, NotInList) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SelfAnchoredMove as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    key = f"chunk_demoted:{demoted_id}" if demoted_id is not None else None
    facts = change.publish(cause="demoted", key=key)
    services.events.publish_queue_changed()  # a demoted chunk leaves the ready queue
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.patch(
    "/chunks/{chunk_id}",
    response_model=ChunkPatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def patch_chunk(chunk_id: str, request: ChunkPatchRequest, services: Services) -> ChunkPatchResponse:
    """Apply the body's fields in one all-or-nothing edit.

    404 for an unknown chunk or an unresolvable graph, 422 for a blank value, 409 for a
    refused edit."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        ChunkPatchBody(request, services).apply(chunk)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (
        ChunkNotEditable,
        ChunkAlreadyMoved,
        TargetGraphRetired,
        MigrationTargetIsCurrentPin,
        ForcedNodeUnknown,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    updated = services.chunks.record.get(chunk_id)
    assert updated is not None, "the chunk existed a moment ago and this edit does not delete chunks"
    facts = change.publish(cause="edited")
    return ChunkPatchResponse(
        chunk_id=chunk_id,
        graph_id=updated.graph_id,
        default_model=list(updated.default_model),
        default_effort=updated.default_effort,
        default_harnesses=list(updated.default_harnesses),
        intended_migration=ChunkView.of(services, updated, facts=facts).intended_migration(),
        **project_label_fields(
            updated.project_id, services.projects.get(updated.project_id) if updated.project_id is not None else None
        ),
    )


@router.delete(
    "/chunks/{chunk_id}",
    response_model=ChunkDeleteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def delete_chunk(chunk_id: str, request: ChunkDeleteRequest, services: Services) -> ChunkDeleteResponse:
    """Delete an unacquired CHUNK, withdrawing every open ``hub:``-source item it holds
    in the same write. 404 for an unknown chunk or one a race deletes
    between resolving it and this write; 409 for one held, terminal, or a standing
    prerequisite for another chunk, naming the dependents in that case.
    Irreversible: CHUNK is gone from every read the instant this returns."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        deleted_id = services.delete.delete(chunk, by=request.by)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChunkNotDeletable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ChunkHasDependents as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    change.publish(cause="deleted", key=f"chunk_deleted:{deleted_id}", by=request.by, status=change.prev_status)
    services.events.publish_queue_changed()  # a deleted chunk is never offered for claim again
    return ChunkDeleteResponse(chunk_id=chunk_id)


def _author_view(author: AuthorView, runner_names: Mapping[str, str]) -> WorkItemAuthorView:
    """A seam-level :class:`AuthorView` onto the wire — no vocabulary resolved here: the
    source already resolved it, this only reshapes the fields and names a fleet author's
    runner out of ``runner_names``, the caller's one batched read."""
    return WorkItemAuthorView(
        kind=author.kind,
        user_id=author.user_id,
        login=author.login,
        runner_id=author.runner_id,
        runner_name=runner_names.get(author.runner_id) if author.runner_id is not None else None,
        chunk_id=author.chunk_id,
        node_name=author.node_name,
    )


@router.get(
    "/chunks/{chunk_id}/work-items",
    response_model=WorkItemsView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def get_work_items(chunk_id: str, services: Services) -> WorkItemsView:
    """Pass-through work items read, one entry per pointer, contents never stored. A
    per-pointer resolution or forge failure becomes that entry's own ``error`` instead of
    failing the whole read; a chunk with no pointers reads as an empty list, not a 404."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    fetched_at = iso_utc(services.clock.now())
    holders = services.chunks.work_refs.live_holders(chunk.work_refs)
    # Each entry beside its fetched author, so one read names every fleet author afterwards.
    entries: list[tuple[WorkItemEntry, AuthorView | None]] = []
    for pointer in chunk.work_refs:
        source = services.work_sources.get(pointer.source)
        named = WorkItemEntry(
            source=pointer.source,
            ref=pointer.ref,
            slug=services.work_sources.source_label(pointer.source),
            hub_source=pointer.source == RESERVED_HUB_SOURCE_NAME,
            fetched_at=fetched_at,
        )
        if source is None:
            entries.append(
                (
                    named.model_copy(
                        update={
                            "label": services.work_sources.label(pointer),
                            "error": f"no work source is configured under {pointer.source!r}",
                        }
                    ),
                    None,
                )
            )
            continue
        label = source.label(pointer) or services.work_sources.label(pointer)
        web_url = source.web_url(pointer, live_holder=holders.get(pointer))
        try:
            item = source.fetch(pointer)
            stated_priority = WorkItemPriority(item.stated_priority) if item.stated_priority is not None else None
        except (WorkSourceError, ValueError) as exc:
            entries.append((named.model_copy(update={"label": label, "web_url": web_url, "error": str(exc)}), None))
        else:
            entries.append(
                (
                    named.model_copy(
                        update={
                            "label": label,
                            "web_url": web_url,
                            "title": item.title,
                            "body": item.body,
                            "comments": item.comments,
                            "stated_priority": stated_priority,
                        }
                    ),
                    item.author,
                )
            )
    runner_names = services.registry.names_for(
        author.runner_id for _, author in entries if author is not None and author.runner_id is not None
    )
    return WorkItemsView(
        items=[
            entry if author is None else entry.model_copy(update={"author": _author_view(author, runner_names)})
            for entry, author in entries
        ]
    )


# `/pm-items` is a deprecated alias onto the *same handler* as `/work-items`:
# an HTTP path is reachable by out-of-tree clients we do not ship and cannot redeploy.
router.add_api_route(
    "/chunks/{chunk_id}/pm-items",
    get_work_items,
    methods=["GET"],
    response_model=WorkItemsView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
    deprecated=True,
    name="get_pm_items_deprecated_alias",
    summary="Deprecated alias for GET /chunks/{chunk_id}/work-items",
    description=(
        "Deprecated: use `GET /chunks/{chunk_id}/work-items`, which this "
        "path aliases onto the identical handler and returns the identical view."
    ),
)
