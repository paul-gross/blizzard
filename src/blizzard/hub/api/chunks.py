"""Chunk routes — the anonymous **operator** surface (issue #87).

Controllers stay read-only over the store (``bzh:controller-read-only``); list/detail
reads derive status and current node from facts (``bzh:facts-not-status``), never a
stored column. The work-item read is a pass-through whose contents are never stored.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import CHUNK_CONTROL, CHUNK_INGEST, FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.chunk_edit import ChunkPatchBody
from blizzard.hub.api.chunk_views import ChunkView
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.graph_names import GraphNames
from blizzard.hub.api.marker_auth import require_marker_authority
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.decisions import NotEscalated
from blizzard.hub.domain.detach import NotRouted
from blizzard.hub.domain.edit import (
    ChunkAlreadyMoved,
    ChunkNotEditable,
    ForcedNodeUnknown,
    MigrationTargetIsCurrentPin,
    TargetGraphRetired,
)
from blizzard.hub.domain.graph_authoring import DefaultGraphRetired
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.pause import ChunkNotPausable
from blizzard.hub.domain.stop import ChunkNotStoppable
from blizzard.hub.domain.work import (
    ChunkFacts,
    FleetSummary,
    WorkRef,
)
from blizzard.hub.work_sources.source import WorkSourceError
from blizzard.wire.chunk import (
    ChunkDetail,
    ChunkIngestConflict,
    ChunkIngestRequest,
    ChunkIngestResponse,
    ChunkPatchRequest,
    ChunkPatchResponse,
    ChunkPauseRequest,
    ChunkStopRequest,
    ChunkSummary,
    HubMarkerRequest,
    HubMarkerResponse,
    WorkItemEntry,
    WorkItemsView,
)
from blizzard.wire.fleet import FleetSummaryView

router = APIRouter(prefix="/api", tags=["chunks"], dependencies=[Depends(reject_runner_principal)])


@dataclass(frozen=True)
class OpenDecision:
    """A chunk's graph gate as the board must hear about it (issue #87)."""

    services: HubServices
    chunk_id: str

    def publish(self) -> None:
        """Emit ``decision-opened`` if the chunk now carries a live, unresolved gate."""
        decision = self.services.chunks.decision_for_chunk(self.chunk_id)
        if decision is not None and not decision.resolved and not decision.transitioned:
            self.services.events.publish_decision_opened(
                self.chunk_id, decision.decision_id, key=f"decisions:{decision.decision_id}"
            )


@router.post(
    "/chunks",
    response_model=ChunkIngestResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require(CHUNK_INGEST))],
)
def ingest_chunk(request: ChunkIngestRequest, services: Annotated[HubServices, Depends(get_services)]) -> object:
    """Ingest by source-native token; 422 on a token no configured source
    claims; 409 on a pointer held by a live chunk; 503 if every graph named after the
    packaged default has been retired (issue #101 — the operator's brake, not a code
    bug: re-enable one or mint a new one)."""
    if not request.tokens:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="at least one token required")
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
    # A freshly ingested chunk rests ``not_ready`` — visible on the board but not
    # in the ready queue, so no ``queue-changed`` fires until it is promoted.
    chunk_events.publish_chunk_changed(services, chunk_id, cause="minted", prev_status=None, key=f"chunks:{chunk_id}")
    return ChunkIngestResponse(chunk_id=chunk_id)


@router.get("/chunks", response_model=list[ChunkSummary], dependencies=[Depends(require(FLEET_VIEW))])
def list_chunks(services: Annotated[HubServices, Depends(get_services)]) -> list[ChunkSummary]:
    """The fleet chunk list — derived status per chunk."""
    names = GraphNames(services.graphs.get)
    return [ChunkView.of(services, chunk, names).summary() for chunk in services.chunks.list_all()]


@dataclass(frozen=True)
class FleetPulse:
    """Every chunk's derived status folded to the four fleet-summary counts (issue #76)."""

    services: HubServices

    def view(self) -> FleetSummaryView:
        """Not a route of its own here. Derives each chunk's status the same way
        :func:`list_chunks` does, but yields only the four bucket integers, so the payload
        is a fixed four numbers regardless of fleet size."""
        summary = FleetSummary.of(
            (self.services.chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)).status()
            for chunk in self.services.chunks.list_all()
        )
        return FleetSummaryView(
            ready=summary.ready,
            running=summary.running,
            waiting=summary.waiting,
            needs=summary.needs,
        )


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail, dependencies=[Depends(require(FLEET_VIEW))])
def get_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkDetail:
    """One chunk aggregate in full — derived status, current node, route."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    return ChunkView.of(services, chunk).detail()


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
    chunk = services.chunks.get(chunk_id)
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
    "/chunks/{chunk_id}/requeues",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def requeue_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
    """Close an escalation by supersession: requeue at the current node."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    try:
        requeue_id = services.requeue.requeue(chunk_id)
    except NotEscalated as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    chunk_events.publish_chunk_changed(
        services, chunk_id, cause="requeued", prev_status=prev_status, key=f"requeues:{requeue_id}"
    )
    services.events.publish_queue_changed()  # requeue can re-admit the chunk to the queue
    return ChunkView.of(services, chunk).summary()


@router.post(
    "/chunks/{chunk_id}/detach",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def detach_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
    """Forcibly release a chunk from its runner without touching any escalation."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    try:
        released_id = services.detach.detach(chunk)
    except NotRouted as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    chunk_events.publish_chunk_changed(
        services, chunk_id, cause="detached", prev_status=prev_status, key=f"route_released:{released_id}"
    )
    services.events.publish_queue_changed()  # a detached chunk re-enters the ready queue
    return ChunkView.of(services, chunk).summary()


@router.post(
    "/chunks/{chunk_id}/pause",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def pause_chunk(
    chunk_id: str, request: ChunkPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkSummary:
    """Set a chunk's operator pause brake — the claim is kept, unlike detach (issue #46)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    try:
        pause_fact_id = services.pause.pause(chunk, by=request.by)
    except ChunkNotPausable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    chunk_events.publish_chunk_changed(
        services, chunk_id, cause="paused", prev_status=prev_status, key=f"chunk_pause_facts:{pause_fact_id}"
    )
    services.events.publish_queue_changed()  # a pause moves the chunk out of the ready queue (issue #46)
    return ChunkView.of(services, chunk).summary()


@router.post(
    "/chunks/{chunk_id}/resume",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def resume_chunk(
    chunk_id: str, request: ChunkPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkSummary:
    """Clear a chunk's operator pause brake — idempotent, never refused (issue #46)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    pause_fact_id = services.pause.resume(chunk, by=request.by)
    chunk_events.publish_chunk_changed(
        services, chunk_id, cause="resumed", prev_status=prev_status, key=f"chunk_pause_facts:{pause_fact_id}"
    )
    services.events.publish_queue_changed()  # a resume can re-admit the chunk to the queue (issue #46)
    return ChunkView.of(services, chunk).summary()


@router.post(
    "/chunks/{chunk_id}/stop",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def stop_chunk(
    chunk_id: str, request: ChunkStopRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkSummary:
    """Terminally abandon CHUNK — the operator's last-resort verb (issue #118).

    Records the ``chunk_stopped`` fact so the chunk derives ``stopped`` and never
    re-derives ``ready``, and releases any live route in the same operation. 409 when
    the chunk is already ``done`` or ``stopped`` — stopping is not retroactive."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    try:
        stopped_id = services.stop.stop(chunk, by=request.by)
    except ChunkNotStoppable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    chunk_events.publish_chunk_changed(
        services, chunk_id, cause="stopped", prev_status=prev_status, key=f"chunk_stopped:{stopped_id}"
    )
    services.events.publish_queue_changed()  # a stopped chunk is never offered for claim again
    return ChunkView.of(services, chunk).summary()


@router.post(
    "/chunks/{chunk_id}/promote",
    response_model=ChunkSummary,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def promote_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkSummary:
    """Promote a not-ready chunk to ready so a runner may claim it.

    Idempotent: promoting an already-ready or already-running chunk is a harmless no-op.
    404 only when the chunk is unknown."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    promoted_id = services.promote.promote(chunk_id)
    key = f"chunk_promoted:{promoted_id}" if promoted_id is not None else None
    chunk_events.publish_chunk_changed(services, chunk_id, cause="promoted", prev_status=prev_status, key=key)
    services.events.publish_queue_changed()  # a promoted chunk enters the ready queue
    return ChunkView.of(services, chunk).summary()


@router.patch(
    "/chunks/{chunk_id}",
    response_model=ChunkPatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def patch_chunk(
    chunk_id: str, request: ChunkPatchRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkPatchResponse:
    """Apply the body's fields in one all-or-nothing edit (issue #124).

    404 only when the chunk is unknown; :class:`ChunkPatchBody` and ``EditService.edit``
    own every other refusal."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)
    try:
        ChunkPatchBody(request, services).apply(chunk)
    except (
        ChunkNotEditable,
        ChunkAlreadyMoved,
        TargetGraphRetired,
        MigrationTargetIsCurrentPin,
        ForcedNodeUnknown,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    updated = services.chunks.get(chunk_id)
    assert updated is not None, "the chunk existed a moment ago and this edit does not delete chunks"
    chunk_events.publish_chunk_changed(services, chunk_id, cause="edited", prev_status=prev_status)
    return ChunkPatchResponse(
        chunk_id=chunk_id,
        graph_id=updated.graph_id,
        default_model=list(updated.default_model),
        default_effort=updated.default_effort,
        intended_migration=ChunkView.of(services, updated).intended_migration(),
    )


@router.get("/chunks/{chunk_id}/work-items", response_model=WorkItemsView, dependencies=[Depends(require(FLEET_VIEW))])
def get_work_items(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> WorkItemsView:
    """Pass-through work items read — one entry per pointer, contents never stored.

    A per-pointer resolution or forge failure degrades to an ``error`` on that entry
    rather than failing the whole read. A chunk with no pointers is an empty list, not
    a 404; no configured work source at all is a 503 up front."""
    if not services.work_sources.names():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no work source is configured")
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    fetched_at = iso_utc(services.clock.now())
    entries: list[WorkItemEntry] = []
    for pointer in chunk.work_refs:
        source = services.work_sources.get(pointer.source)
        if source is None:
            entries.append(
                WorkItemEntry(
                    source=pointer.source,
                    ref=pointer.ref,
                    label=None,
                    web_url=None,
                    fetched_at=fetched_at,
                    error=f"no configured work source named {pointer.source!r}",
                )
            )
            continue
        label = source.label(pointer)
        web_url = source.web_url(pointer)
        try:
            item = source.fetch(pointer)
        except WorkSourceError as exc:
            entries.append(
                WorkItemEntry(
                    source=pointer.source,
                    ref=pointer.ref,
                    label=label,
                    web_url=web_url,
                    fetched_at=fetched_at,
                    error=str(exc),
                )
            )
        else:
            entries.append(
                WorkItemEntry(
                    source=pointer.source,
                    ref=pointer.ref,
                    label=label,
                    web_url=web_url,
                    fetched_at=fetched_at,
                    title=item.title,
                    body=item.body,
                    comments=item.comments,
                )
            )
    return WorkItemsView(items=entries)


# `/pm-items` is a deprecated alias onto the *same handler* as `/work-items` (issue #55):
# an HTTP path is reachable by out-of-tree clients we do not ship and cannot redeploy.
router.add_api_route(
    "/chunks/{chunk_id}/pm-items",
    get_work_items,
    methods=["GET"],
    response_model=WorkItemsView,
    dependencies=[Depends(require(FLEET_VIEW))],
    deprecated=True,
    name="get_pm_items_deprecated_alias",
    summary="Deprecated alias for GET /chunks/{chunk_id}/work-items",
    description=(
        "Deprecated since issue #55 — use `GET /chunks/{chunk_id}/work-items`, which this "
        "path aliases onto the identical handler and returns the identical view."
    ),
)
