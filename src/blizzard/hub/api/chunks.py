"""Chunk routes — the anonymous **operator** surface.

Controllers stay read-only over the store (``bzh:controller-read-only``); list/detail
reads derive status and current node from facts (``bzh:facts-not-status``), never a
stored column. The work-item read is a pass-through whose contents are never stored.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.chunk_status import TERMINAL_STATUSES, ChunkStatus
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.work_items import WorkItemPriority
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.chunk_edit import ChunkPatchBody
from blizzard.hub.api.chunk_views import ChunkView, blocked_view
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.graph_names import GraphNames, graph_by_ref
from blizzard.hub.api.marker_auth import require_marker_authority
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.chunk.delivery_read import DeliveryRead, DeliverySources
from blizzard.hub.domain.chunk.dependencies import (
    ChunkNeighbor,
    derive_blocked_prerequisites,
    derive_chunk_neighborhood,
)
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.ingest import EmptyIngest, IngestConflict, ingest_work_refs
from blizzard.hub.domain.chunk.model import Chunk, ChunkFacts, FleetSummary, WorkRef
from blizzard.hub.domain.chunk.ports.work_refs import resolve_live_holders
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
from blizzard.hub.domain.operations.delete import ChunkHasDependents, ChunkNotDeletable
from blizzard.hub.domain.operations.edit import (
    ChunkAlreadyMoved,
    ChunkNotEditable,
    ForcedNodeUnknown,
    MigrationTargetIsCurrentPin,
)
from blizzard.hub.domain.operations.pause import ChunkNotPausable
from blizzard.hub.domain.operations.promote import ChunkNotPromotable
from blizzard.hub.domain.operations.restart import (
    ChunkNotRestartable,
    RestartCurrentNodeUnknown,
    RestartGraphPinChanged,
    RestartNodeUnknown,
)
from blizzard.hub.domain.operations.stop import ChunkNotStoppable
from blizzard.hub.work_sources.source import AuthorView, WorkSourceError
from blizzard.wire.chunk import (
    BlockedView,
    ChunkCompleteRequest,
    ChunkCountsView,
    ChunkDeleteRequest,
    ChunkDeleteResponse,
    ChunkDetail,
    ChunkIngestConflict,
    ChunkIngestRequest,
    ChunkIngestResponse,
    ChunkNeighborhoodView,
    ChunkNeighborView,
    ChunkPatchRequest,
    ChunkPatchResponse,
    ChunkPauseRequest,
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
from blizzard.wire.work_source import WorkItemAuthorView

#: How long a finished ``done`` chunk stays on ``GET /api/chunks?board_window=true``.
BOARD_DONE_WINDOW = timedelta(hours=48)

router = APIRouter(prefix="/api", tags=["chunks"], dependencies=[Depends(reject_runner_principal)])

#: A delivery the write fence refused — a 409, never ``invalid``, which would write a failure marker.
_FENCED_DELIVERY_DETAIL = "delivery superseded: the chunk is terminal or was restarted"


@dataclass(frozen=True)
class OpenDecision:
    """A chunk's graph gate."""

    services: HubServices
    chunk_id: str

    def publish(self) -> None:
        """Emit ``decision-opened`` if the chunk now carries a live, unresolved gate."""
        decision = self.services.chunks.decisions.decision_for_chunk(self.chunk_id)
        if decision is not None and decision.is_open:
            self.services.events.publish_decision_opened(
                self.chunk_id, decision.decision_id, key=f"decisions:{decision.decision_id}"
            )


@router.post(
    "/chunks",
    response_model=ChunkIngestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(Permission.CHUNK_INGEST))],
)
def ingest_chunk(request: ChunkIngestRequest, services: Annotated[HubServices, Depends(get_services)]) -> object:
    """Ingest by source-native token; 422 on a token no configured source
    claims; 409 on a pointer held by a live chunk; 503 if every graph named after the
    packaged default has been retired (the operator's brake, not a code
    bug: re-enable one or mint a new one)."""
    # Resolution before minting, and before the live-holder check: an unresolvable
    # token should not consult the store, and the request rejects as a whole.
    pointers: list[WorkRef] = []
    for token in request.tokens:
        pointer = services.work_sources.resolve(token)
        if pointer is None:
            configured = ", ".join(sorted(services.work_sources.names())) or "none"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(f"token {token!r} is not claimed by any configured work source (configured: {configured})"),
            )
        pointers.append(pointer)
    try:
        # The batch is refused before the default graph resolves, which may mint one.
        pointers = ingest_work_refs(pointers)
    except EmptyIngest as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    try:
        graph = services.graph_mint.ensure_default(
            services.default_graph_doc, definition_yaml=services.default_graph_yaml
        )
    except DefaultGraphRetired as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    try:
        chunk_id = services.ingest.ingest(pointers, graph=graph)
    except IngestConflict as exc:
        conflict = ChunkIngestConflict(
            existing_chunk_id=exc.existing_chunk_id, source=exc.pointer.source, ref=exc.pointer.ref
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    chunk_events.ChunkChanged.of(services, chunk_id, prev_status=None).publish(cause="minted", key=f"chunks:{chunk_id}")
    services.events.publish_queue_changed()  # mint adds the chunk to the backlog list
    return ChunkIngestResponse(chunk_id=chunk_id)


@router.get("/chunks", response_model=ChunksPageView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_chunks(
    services: Annotated[HubServices, Depends(get_services)],
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    board_window: Annotated[bool, Query()] = False,
) -> ChunksPageView:
    """The fleet chunk list — derived status per chunk, bounded and keyset-paginated.

    Only the page's own rows render, but live-holder and blocked-marking derivation still
    see the whole fleet — a pointer this page renders can be held live by a
    chunk outside it, same for a dependent's prerequisite."""
    # ``board_window`` drops ``done`` chunks finished before the window; a page can come back short.
    done_since = services.clock.now() - BOARD_DONE_WINDOW if board_window else None
    names = GraphNames(services.graphs)
    facts = services.chunks.facts.load_all_facts()
    routes = services.chunks.route.load_all_routes()
    # The dependency edges join the same bulk facts pass at this call site, not
    # inside a store (``bzh:dependency-inversion``).
    statuses = {chunk_id: chunk_facts.status() for chunk_id, chunk_facts in facts.items()}
    markings = derive_blocked_prerequisites(services.chunks.dependencies.list_standing_edges(), statuses)
    try:
        page = services.chunks.record.list_page(cursor=cursor, limit=limit, done_since=done_since)
    except MalformedCursor as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed cursor") from exc
    if done_since is not None:
        # The store's exclusion leaves a same-instant movement tie in; the derivation settles it.
        page_chunks = [
            c for c in page.chunks if not ChunkFacts.or_default(facts.get(c.chunk_id)).finished_before(done_since)
        ]
    else:
        page_chunks = page.chunks
    # Live-holder resolution needs every chunk's pointers, not just this page's —
    # narrowing to the page could miss a pointer another, unlisted chunk holds live.
    all_chunks = services.chunks.record.list_all()
    # One priming call resolves the page's own pinned graphs' name/entry-node/node-names
    # up front — narrowed to the page, since nothing outside it is rendered.
    names.prime(chunk.graph_id for chunk in page_chunks)
    # Unlike the fleet-wide status/holder reads above, delivery belongs only to
    # rendered rows: one narrowed, batched read over this page's ids.
    delivery_sources = services.chunks.artifacts.delivery_sources_for([chunk.chunk_id for chunk in page_chunks])
    # Derives from the chunks and statuses already loaded above, no further fact load.
    live_holders = resolve_live_holders(
        ((p, chunk.chunk_id) for chunk in all_chunks for p in chunk.work_refs), statuses
    )
    # The page's routed runners' registered names — one batched read, narrowed to the page.
    runner_names = services.registry.names_for(
        route.runner_id for chunk in page_chunks if (route := routes.get(chunk.chunk_id)) is not None
    )
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
                ),
                runner_names=runner_names,
            ).summary()
            for chunk in page_chunks
        ],
        next_cursor=page.next_cursor,
    )


@router.get("/chunk-counts", response_model=ChunkCountsView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def chunk_counts(services: Annotated[HubServices, Depends(get_services)]) -> ChunkCountsView:
    """The all-time fleet count per derived status — over exactly the chunks
    ``GET /api/chunks`` pages over, with no window applied."""
    counts = services.chunks.facts.status_counts()
    return ChunkCountsView(
        total=sum(counts.values()),
        terminal=sum(n for st, n in counts.items() if st in TERMINAL_STATUSES),
        **{st.value: n for st, n in counts.items()},
    )


def _neighbor_view(neighbor: ChunkNeighbor) -> ChunkNeighborView:
    return ChunkNeighborView(chunk_id=neighbor.chunk_id, status=neighbor.status, satisfied=neighbor.satisfied)


def _dependency_views_for_chunk(
    services: HubServices, chunk_id: str, *, status: ChunkStatus
) -> tuple[BlockedView | None, ChunkNeighborhoodView]:
    """``GET /api/chunks/{chunk_id}``'s blocked marking and neighborhood — both derived
    from the one standing-edges read and one statuses map, since a chunk's dependent edges are a subset of its own
    neighborhood edges and reading them separately would risk a release or completion landing between the two reads.
    ``status`` is the caller's own already-derived value for ``chunk_id``, so this need not reload its facts a second
    time."""
    edges = services.chunks.dependencies.standing_edges_for(chunk_id)
    if not edges:
        return None, ChunkNeighborhoodView(prerequisites=[], dependents=[])
    statuses: dict[str, ChunkStatus] = {chunk_id: status}
    neighbor_ids = {
        edge.prerequisite_chunk_id if edge.dependent_chunk_id == chunk_id else edge.dependent_chunk_id for edge in edges
    }
    neighbor_facts_by_id = services.chunks.facts.load_facts_for(list(neighbor_ids))
    statuses.update((neighbor_id, facts.status()) for neighbor_id, facts in neighbor_facts_by_id.items())
    dependent_edges = [e for e in edges if e.dependent_chunk_id == chunk_id]
    blocked = blocked_view(derive_blocked_prerequisites(dependent_edges, statuses).get(chunk_id))
    neighborhood = derive_chunk_neighborhood(chunk_id, edges, statuses)
    return blocked, ChunkNeighborhoodView(
        prerequisites=[_neighbor_view(n) for n in neighborhood.prerequisites],
        dependents=[_neighbor_view(n) for n in neighborhood.dependents],
    )


@dataclass(frozen=True)
class FleetPulse:
    """Every chunk's derived status folded to the four fleet-summary counts."""

    services: HubServices

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
def get_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkDetail:
    """One chunk aggregate in full — derived status, current node, route."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    facts = services.chunks.facts.load_facts(chunk_id) or ChunkFacts(minted=True)
    chunk_status = facts.status()
    blocked, neighborhood = _dependency_views_for_chunk(services, chunk_id, status=chunk_status)
    # Primed with every graph id this chunk's history ever names.
    names = GraphNames(services.graphs)
    names.prime(_detail_graph_ids(chunk, facts))
    return ChunkView.of(services, chunk, names=names, blocked=blocked, facts=facts, neighborhood=neighborhood).detail()


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
    dependencies=[Depends(require_marker_authority)],
)
def record_hub_marker(
    chunk_id: str,
    node_id: str,
    epoch: int,
    request_body: HubMarkerRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> HubMarkerResponse:
    """The mid-run marker callback (#65) — a ``run:`` step's own dynamic-loop marker.

    Records a marker artifact mid-run, ahead of the producing command's own exit.
    Idempotent per ``(chunk, node, name, epoch)``.
    """
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    node = graph.node_by_id(node_id) if graph is not None else None
    node_name = node.name if node is not None else node_id
    recorded = services.hub_node.record_marker(
        chunk_id,
        node_id=node_id,
        node_name=node_name,
        epoch=epoch,
        name=request_body.name,
        content=request_body.content,
    )
    return HubMarkerResponse(recorded=recorded, chunk_id=chunk_id, name=request_body.name)


@router.post(
    "/chunks/{chunk_id}/garden-delivery",
    response_model=GardenDeliveryResponse,
    dependencies=[Depends(require_marker_authority)],
)
def record_garden_delivery(
    chunk_id: str,
    node_id: str,
    epoch: int,
    request_body: GardenDeliveryRequest,
    services: Annotated[HubServices, Depends(get_services)],
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
    dependencies=[Depends(require_marker_authority)],
)
def record_review_findings_delivery(
    chunk_id: str,
    node_id: str,
    epoch: int,
    services: Annotated[HubServices, Depends(get_services)],
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
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def requeue_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
    """Close an escalation by supersession: requeue at the current node."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        requeue_id = services.requeue.requeue(chunk)
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
def restart_chunk(
    chunk_id: str, request: ChunkRestartRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkSummary:
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
        restart_id = services.restart.restart(chunk, graph, node_name=request.node, by=request.by, to_graph=to_graph)
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
    facts = change.publish(cause="restarted", key=f"chunk_restarts:{restart_id}")
    services.events.publish_queue_changed()  # an unrouted chunk re-enters the queue at the target node
    # Re-read: a cross-graph move re-pinned the chunk, and the row's node name resolves off that pin.
    return ChunkView.of(services, services.chunks.record.get(chunk_id) or chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/detach",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def detach_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
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
def pause_chunk(
    chunk_id: str, request: ChunkPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkSummary:
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
def resume_chunk(
    chunk_id: str, request: ChunkPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkSummary:
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
def stop_chunk(
    chunk_id: str, request: ChunkStopRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkSummary:
    """Terminally abandon CHUNK — the operator's last-resort verb.

    Records the ``chunk_stopped`` fact so the chunk derives ``stopped`` and never
    re-derives ``ready``, releases any live route, and supersedes any open escalation. 409
    when the chunk is already ``done`` or ``stopped`` — stopping is not retroactive."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        stopped_id = services.stop.stop(chunk, by=request.by)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChunkNotStoppable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = change.publish(cause="stopped", key=f"chunk_stopped:{stopped_id}")
    services.events.publish_queue_changed()  # a stopped chunk is never offered for claim again
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/complete",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def complete_chunk(
    chunk_id: str, request: ChunkCompleteRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkSummary:
    """Manually complete CHUNK, from any non-``done`` status, including ``stopped``.
    Records the ``chunk_completed`` fact so the chunk derives ``done``, releases any live route
    and held hub-exec slot, and makes the chunk's work refs eligible for closure. Idempotent:
    completing an already-``done`` chunk is a harmless no-op. 404 only when the chunk is
    unknown."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        completed_id = services.complete.complete(chunk, by=request.by)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    key = f"chunk_completed:{completed_id}" if completed_id is not None else None
    facts = change.publish(cause="completed", key=key)
    services.events.publish_queue_changed()  # a completed chunk is never offered for claim again
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.post(
    "/chunks/{chunk_id}/promote",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def promote_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
    """Promote a not-ready chunk to ready so a runner may claim it.

    Idempotent: promoting an already-promoted chunk is a harmless no-op. 404 when the chunk
    is unknown; 409 when a never-promoted chunk is already ``done`` or ``stopped``."""
    chunk = services.chunks.record.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    change = chunk_events.ChunkChanged.before(services, chunk_id)
    statuses = services.chunks.facts.load_live_statuses()
    try:
        promoted_id = services.promote.promote(chunk, statuses=statuses)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChunkNotPromotable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    key = f"chunk_promoted:{promoted_id}" if promoted_id is not None else None
    facts = change.publish(cause="promoted", key=key)
    services.events.publish_queue_changed()  # a promoted chunk enters the ready queue
    return ChunkView.of(services, chunk, facts=facts).summary()


@router.patch(
    "/chunks/{chunk_id}",
    response_model=ChunkPatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def patch_chunk(
    chunk_id: str, request: ChunkPatchRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkPatchResponse:
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
    )


@router.delete(
    "/chunks/{chunk_id}",
    response_model=ChunkDeleteResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def delete_chunk(
    chunk_id: str, request: ChunkDeleteRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkDeleteResponse:
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
def get_work_items(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> WorkItemsView:
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
        hub_source = pointer.source == RESERVED_HUB_SOURCE_NAME
        if source is None:
            entries.append(
                (
                    WorkItemEntry(
                        source=pointer.source,
                        ref=pointer.ref,
                        hub_source=hub_source,
                        label=None,
                        web_url=None,
                        fetched_at=fetched_at,
                        error=f"no configured work source named {pointer.source!r}",
                    ),
                    None,
                )
            )
            continue
        label = source.label(pointer)
        web_url = source.web_url(pointer, live_holder=holders.get(pointer))
        try:
            item = source.fetch(pointer)
            stated_priority = WorkItemPriority(item.stated_priority) if item.stated_priority is not None else None
        except (WorkSourceError, ValueError) as exc:
            entries.append(
                (
                    WorkItemEntry(
                        source=pointer.source,
                        ref=pointer.ref,
                        hub_source=hub_source,
                        label=label,
                        web_url=web_url,
                        fetched_at=fetched_at,
                        error=str(exc),
                    ),
                    None,
                )
            )
        else:
            entries.append(
                (
                    WorkItemEntry(
                        source=pointer.source,
                        ref=pointer.ref,
                        hub_source=hub_source,
                        label=label,
                        web_url=web_url,
                        fetched_at=fetched_at,
                        title=item.title,
                        body=item.body,
                        comments=item.comments,
                        stated_priority=stated_priority,
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
