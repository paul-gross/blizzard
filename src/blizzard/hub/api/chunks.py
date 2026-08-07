"""Chunk routes — the anonymous **operator** surface (issue #87).

Controllers stay read-only over the store (``bzh:controller-read-only``); list/detail
reads derive status and current node from facts (``bzh:facts-not-status``), never a
stored column. The work-item read is a pass-through whose contents are never stored.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import CHUNK_CONTROL, CHUNK_INGEST, FLEET_VIEW
from blizzard.foundation.ids import minted_at
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.decisions import to_decision_view
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.marker_auth import require_marker_authority
from blizzard.hub.api.questions import question_view
from blizzard.hub.composition import HubServices
from blizzard.hub.delivery.hub_node import poll_interval_for
from blizzard.hub.domain.artifacts import ArtifactRow, GitCommitArtifact, from_row, store_key
from blizzard.hub.domain.decisions import NotEscalated
from blizzard.hub.domain.detach import NotRouted
from blizzard.hub.domain.edit import (
    UNSET,
    ChunkAlreadyMoved,
    ChunkEdit,
    ChunkNotEditable,
    ForcedNodeUnknown,
    MigrationTargetIsCurrentPin,
    TargetGraphRetired,
)
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.graph_authoring import DefaultGraphRetired
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.pause import ChunkNotPausable
from blizzard.hub.domain.stop import ChunkNotStoppable
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    IntendedMigration,
    MigrationMode,
    WorkRef,
    derive_fleet_summary,
    holds_claim,
)
from blizzard.hub.work_sources.source import IWorkSource, IWorkSourceRegistry, WorkSourceError
from blizzard.wire.chunk import (
    ArtifactView,
    BounceView,
    ChunkDetail,
    ChunkIngestConflict,
    ChunkIngestRequest,
    ChunkIngestResponse,
    ChunkPatchRequest,
    ChunkPatchResponse,
    ChunkPauseRequest,
    ChunkStopRequest,
    ChunkSummary,
    ChunkUsageTotalView,
    ChunkUsageView,
    EscalationView,
    HubMarkerRequest,
    HubMarkerResponse,
    IntendedMigrationView,
    MigrationView,
    PauseView,
    PendingView,
    PrView,
    RouteView,
    TransitionView,
    WorkItemEntry,
    WorkItemsView,
    WorkRefView,
)
from blizzard.wire.fleet import FleetSummaryView

router = APIRouter(prefix="/api", tags=["chunks"], dependencies=[Depends(reject_runner_principal)])


def _pointer_views(chunk: Chunk, work_sources: IWorkSourceRegistry) -> list[WorkRefView]:
    """Each pointer with its board-legible label and browser URL —
    both null when no configured source names ``pointer.source``.

    Each pointer is resolved to its own binding by name (``work_sources.get(p.source)``) — a
    chunk's pointers need not all share one source."""
    views: list[WorkRefView] = []
    for p in chunk.work_refs:
        source = work_sources.get(p.source)
        views.append(
            WorkRefView(
                source=p.source,
                ref=p.ref,
                label=source.label(p) if source is not None else None,
                web_url=source.web_url(p) if source is not None else None,
            )
        )
    return views


def publish_open_decision(services: HubServices, chunk_id: str) -> None:
    """Emit ``decision-opened`` if the chunk now carries a live, unresolved gate (issue #87)."""
    decision = services.chunks.decision_for_chunk(chunk_id)
    if decision is not None and not decision.resolved and not decision.transitioned:
        services.events.publish_decision_opened(chunk_id, decision.decision_id, key=f"decisions:{decision.decision_id}")


def _node_name(graph: Graph | None, node_id: str | None) -> str | None:
    """The human graph name for ``node_id`` in ``graph``, or ``None`` when unresolvable."""
    if graph is None or node_id is None:
        return None
    node = graph.node_by_id(node_id)
    return node.name if node is not None else None


def _graph_name(graph: Graph | None) -> str | None:
    return graph.name if graph is not None else None


def _history_views(facts: ChunkFacts, graphs: dict[str | None, Graph | None]) -> list[TransitionView]:
    """The chunk's transitions oldest-first.

    Each edge's node ids resolve against *the graph the transition happened in* (issue
    #90), keyed by ``TransitionFact.graph_id`` — not the chunk's current pin (pinned by
    ``tests/test_transition_graph_provenance.py``)."""
    views: list[TransitionView] = []
    for t in facts.transition_history():
        graph = graphs.get(t.graph_id)
        views.append(
            TransitionView(
                from_node_id=t.from_node_id,
                from_node_name=_node_name(graph, t.from_node_id),
                to_node_id=t.to_node_id,
                to_node_name=_node_name(graph, t.to_node_id),
                choice_name=t.choice_name,
                epoch=t.epoch,
                recorded_at=iso_utc(t.recorded_at),
                graph_id=t.graph_id,
                graph_name=_graph_name(graph),
            )
        )
    return views


def _migration_views(facts: ChunkFacts, graphs: dict[str | None, Graph | None]) -> list[MigrationView]:
    """The chunk's cross-graph migration steps oldest-first (issue #90).

    Each step names the graph it left and the graph it re-pinned to: ``from_node``
    resolves against the ``from_graph``, ``landed_node`` against the ``to_graph`` — each
    side's own graph, so neither degrades to a raw id when the two differ."""
    views: list[MigrationView] = []
    for m in sorted(facts.migrations, key=lambda m: (m.recorded_at, m.epoch)):
        from_graph = graphs.get(m.from_graph_id)
        to_graph = graphs.get(m.to_graph_id)
        views.append(
            MigrationView(
                from_node_id=m.from_node_id,
                from_node_name=_node_name(from_graph, m.from_node_id),
                from_graph_id=m.from_graph_id,
                from_graph_name=_graph_name(from_graph),
                to_graph_id=m.to_graph_id,
                to_graph_name=_graph_name(to_graph),
                landed_node_id=m.landed_node_id,
                landed_node_name=_node_name(to_graph, m.landed_node_id),
                choice_name=m.choice_name,
                model=m.model,
                source=m.source.value if m.source is not None else None,
                recorded_at=iso_utc(m.recorded_at),
            )
        )
    return views


def _intended_migration_view(services: HubServices, chunk: Chunk) -> IntendedMigrationView | None:
    """The chunk's standing migration intent as a view (issue #124), or ``None`` when no
    intent is set. ``graph_name`` is resolved from the stored ``graph_id`` the same way
    ``_migration_views`` resolves a recorded migration's target name — null when the
    target graph cannot be resolved."""
    intent = chunk.intended_migration
    if intent is None:
        return None
    target_graph = services.graphs.get(intent.graph_id)
    return IntendedMigrationView(
        mode=intent.mode,
        graph_id=intent.graph_id,
        graph_name=_graph_name(target_graph),
        node_name=intent.node_name,
    )


def _resolve_graph_by_id_or_name(services: HubServices, ref: str) -> Graph | None:
    """Resolve a PATCH ``to_graph`` reference (issue #124) — a graph id, tried first, or
    a graph name resolved to the newest enabled graph of that name. Mirrors #90's
    ``_resolve_cross_graph_target`` (``hub/api/fleet.py``) id/name duality, but also
    admits an id — the PATCH caller may already hold one (e.g. round-tripping a value
    read off ``GET``), where a #90 migration edge only ever names a graph."""
    graph = services.graphs.get(ref)
    if graph is not None:
        return graph
    return services.graphs.get_enabled_by_name(ref)


def _history_graphs(services: HubServices, chunk: Chunk, facts: ChunkFacts) -> dict[str | None, Graph | None]:
    """The graphs a chunk's history spans, by id (issue #90).

    The chunk's current pin plus every distinct graph its transitions were recorded in
    and every graph its migrations left or entered — each resolved once."""
    graphs: dict[str | None, Graph | None] = {chunk.graph_id: services.graphs.get(chunk.graph_id)}

    def ensure(graph_id: str | None) -> None:
        if graph_id is not None and graph_id not in graphs:
            graphs[graph_id] = services.graphs.get(graph_id)

    for t in facts.transitions:
        ensure(t.graph_id)
    for m in facts.migrations:
        ensure(m.from_graph_id)
        ensure(m.to_graph_id)
    return graphs


def _usage_total_view(facts: ChunkFacts) -> ChunkUsageTotalView:
    """A chunk's derived usage/cost total, wired onto both the summary and detail views."""
    usage = facts.usage_total()
    return ChunkUsageTotalView(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_tokens,
        cache_create_tokens=usage.cache_create_tokens,
        cost_usd=usage.cost_usd,
        cost_partial=usage.cost_partial,
    )


def _usage_history_views(facts: ChunkFacts) -> list[ChunkUsageView]:
    """The chunk's per-node-step usage facts, oldest first — the detail's future
    cost timeline (issue #59)."""
    return [
        ChunkUsageView(
            node_id=u.node_id,
            epoch=u.epoch,
            kind=u.kind,
            model=u.model,
            input_tokens=u.input_tokens,
            output_tokens=u.output_tokens,
            cache_read_tokens=u.cache_read_tokens,
            cache_create_tokens=u.cache_create_tokens,
            cost_usd=u.cost_usd,
        )
        for u in sorted(facts.usage, key=lambda u: u.recorded_at)
    ]


def _branch_url_source(chunk: Chunk, work_sources: IWorkSourceRegistry) -> IWorkSource | None:
    """The binding a chunk's artifact branch links resolve through.

    One forge per chunk, *declared*: the chunk's first pointer whose ``source`` names a
    configured binding lends its ``branch_url``. ``None`` when no pointer's source is
    configured."""
    for p in chunk.work_refs:
        source = work_sources.get(p.source)
        if source is not None:
            return source
    return None


def _artifact_views(rows: list[ArtifactRow], web_base: IWorkSource | None) -> list[ArtifactView]:
    """The chunk's inline artifact store — every entry, with an asset's content and a
    git-commit's pinned reference surfaced; ordered by ``{node}.{name}.{epoch}``
    so a re-run's later-epoch entry follows its predecessors (append-only history)."""
    views: list[ArtifactView] = []
    for row in sorted(rows, key=lambda r: (r.node_name, r.name, r.epoch)):
        artifact = from_row(row)
        attached = minted_at(row.artifact_id)
        common = {
            "key": store_key(row),
            "kind": row.kind.value,
            "name": row.name,
            "node_id": row.node_id,
            "node_name": row.node_name,
            "epoch": row.epoch,
            "recorded_at": iso_utc(attached) if attached is not None else None,
        }
        if isinstance(artifact, GitCommitArtifact):
            branch_url = web_base.branch_url(artifact.repo, artifact.branch_name) if web_base is not None else None
            views.append(
                ArtifactView(
                    **common,
                    repo=artifact.repo,
                    branch_name=artifact.branch_name,
                    commit_hash=artifact.commit_hash,
                    branch_url=branch_url,
                )
            )
        else:
            views.append(ArtifactView(**common, content=artifact.content))
    return views


def _current_node(
    services: HubServices, chunk: Chunk, facts: ChunkFacts, cache: dict[str, Graph | None]
) -> tuple[str | None, str | None]:
    """The chunk's current node as ``(id, name)`` — the newest transition's target, or
    the pinned graph's entry node before the first transition (a nicer board value than
    ``None``). The name is the node's human graph name, resolved here so the board is
    legible without reassembly; the graph per graph_id is memoised in ``cache``
    so a fleet list resolves each once."""
    if chunk.graph_id not in cache:
        cache[chunk.graph_id] = services.graphs.get(chunk.graph_id)
    graph = cache[chunk.graph_id]
    node_id = facts.current_node_id() or (graph.entry_node_id if graph is not None else None)
    if node_id is None:
        return None, None
    node = graph.node_by_id(node_id) if graph is not None else None
    return node_id, node.name if node is not None else None


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


def _summary_view(
    services: HubServices, chunk: Chunk, *, graph_cache: dict[str, Graph | None] | None = None
) -> ChunkSummary:
    """One chunk's derived fleet-list row (issue #104) — shared by :func:`list_chunks`
    and every transition verb (``promote``/``detach``/``pause``/``resume``/``stop``/
    ``requeues``), so both derive the same row from the same facts (``canon:one-owner``).
    ``graph_cache`` lets a full-list caller memoise each graph lookup across chunks; a
    single-chunk caller (a transition verb) gets a fresh one when it passes none."""
    facts = services.chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
    node_id, node_name = _current_node(services, chunk, facts, graph_cache if graph_cache is not None else {})
    status = facts.status()
    # A finished chunk holds no claim (issue #140) — the rule is `holds_claim`'s. Asked
    # before the read so a terminal chunk costs no `route_of` query at all.
    route = services.chunks.route_of(chunk.chunk_id) if holds_claim(status) else None
    completed_at = facts.completed_at()
    return ChunkSummary(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        status=status,
        current_node_id=node_id,
        current_node_name=node_name,
        work_refs=_pointer_views(chunk, services.work_sources),
        default_model=list(chunk.default_model),
        default_effort=chunk.default_effort,
        runner_id=route.runner_id if route is not None else None,
        environment_count=len(route.environment_ids) if route is not None else 0,
        cost=_usage_total_view(facts),
        completed_at=iso_utc(completed_at) if completed_at is not None else None,
    )


@router.get("/chunks", response_model=list[ChunkSummary], dependencies=[Depends(require(FLEET_VIEW))])
def list_chunks(services: Annotated[HubServices, Depends(get_services)]) -> list[ChunkSummary]:
    """The fleet chunk list — derived status per chunk."""
    graph_cache: dict[str, Graph | None] = {}
    return [_summary_view(services, chunk, graph_cache=graph_cache) for chunk in services.chunks.list_all()]


def fleet_summary(services: HubServices) -> FleetSummaryView:
    """Fold every chunk's derived status into the four fleet-summary counts (issue #76).

    Not a route of its own here. Derives each chunk's status the same way
    :func:`list_chunks` does, but returns only the four bucket integers, so the payload
    is a fixed four numbers regardless of fleet size."""
    summary = derive_fleet_summary(
        (services.chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)).status()
        for chunk in services.chunks.list_all()
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
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    route = services.chunks.route_of(chunk_id)
    escalation = facts.open_escalation()
    pause = facts.open_pause()
    decision = services.chunks.decision_for_chunk(chunk_id)
    graph = services.graphs.get(chunk.graph_id)
    node_id = facts.current_node_id() or (graph.entry_node_id if graph is not None else None)
    node_name = _node_name(graph, node_id)
    web_base = _branch_url_source(chunk, services.work_sources)
    history_graphs = _history_graphs(services, chunk, facts)
    artifacts = services.chunks.load_artifacts(chunk_id)
    pending = facts.hub_node_pending()
    pending_view = None
    if pending is not None:
        pending_node = graph.node_by_id(pending.node_id) if graph is not None else None
        if pending_node is not None:
            next_poll_at = pending.polled_at + poll_interval_for(pending_node)
            pending_view = PendingView(node_name=pending_node.name, next_poll_at=iso_utc(next_poll_at))
    return ChunkDetail(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        graph_name=_graph_name(graph),
        graph_created_at=iso_utc(graph.created_at) if graph is not None else None,
        status=facts.status(),
        current_node_id=node_id,
        current_node_name=node_name,
        latest_epoch=facts.latest_epoch(),
        work_refs=_pointer_views(chunk, services.work_sources),
        default_model=list(chunk.default_model),
        default_effort=chunk.default_effort,
        intended_migration=_intended_migration_view(services, chunk),
        route=RouteView(
            runner_id=route.runner_id,
            workspace_id=route.workspace_id,
            environment_ids=route.environment_ids,
        )
        if route is not None
        else None,
        escalation=EscalationView(
            epoch=escalation.epoch,
            takeover_command=escalation.takeover_command,
            wrapped_takeover_command=escalation.wrapped_takeover_command,
        )
        if escalation is not None
        else None,
        pause=PauseView(by=pause.set_by, set_at=iso_utc(pause.set_at)) if pause is not None else None,
        decision=to_decision_view(decision) if decision is not None else None,
        history=_history_views(facts, history_graphs),
        migrations=_migration_views(facts, history_graphs),
        artifacts=_artifact_views(artifacts, web_base),
        questions=[question_view(q) for q in services.chunks.load_questions(chunk_id)],
        awaiting_external_merge=facts.awaiting_external_merge(),
        open_prs=[PrView(repo=pr.repo, number=pr.number, url=pr.url) for pr in facts.pr_opened],
        cost=_usage_total_view(facts),
        usage=_usage_history_views(facts),
        pending=pending_view,
        landed=facts.has_landed_repos(artifacts),
        bounces=[
            BounceView(cause=b.cause, envelope=b.envelope, recorded_at=iso_utc(b.recorded_at))
            for b in sorted(facts.bounces, key=lambda b: b.recorded_at)
        ],
    )


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
    return _summary_view(services, chunk)


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
    return _summary_view(services, chunk)


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
    return _summary_view(services, chunk)


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
    return _summary_view(services, chunk)


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
    return _summary_view(services, chunk)


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
    return _summary_view(services, chunk)


@router.patch(
    "/chunks/{chunk_id}",
    response_model=ChunkPatchResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def patch_chunk(
    chunk_id: str, request: ChunkPatchRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkPatchResponse:
    """Apply any of ``graph_id``, ``default_model``, ``default_effort``, or
    ``intended_migration`` in one all-or-nothing edit (issue #124).

    404 on an unknown chunk, graph, or migration target; 422 on a blank value; the
    editable-status windows and semantic refusals are ``EditService.edit``'s."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    prev_status = chunk_events.snapshot_chunk_status(services, chunk_id)

    graph_id = UNSET
    graph_target: Graph | None = None
    if request.graph_id is not None:
        graph_target = services.graphs.get(request.graph_id)
        if graph_target is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {request.graph_id}")
        graph_id = graph_target.graph_id

    default_model = UNSET
    if request.default_model is not None:
        entries = [entry.strip() for entry in request.default_model]
        if any(not entry for entry in entries):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="default_model entries must not be blank"
            )
        default_model = entries

    # `default_effort` is nullable-with-meaning: explicit `null` clears the preference and
    # an omitted field leaves it unchanged, which a plain `Optional` cannot tell apart.
    default_effort = UNSET
    if "default_effort" in request.model_fields_set:
        if request.default_effort is None:
            default_effort = None
        else:
            effort_value = request.default_effort.strip()
            if not effort_value:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="default_effort must not be blank"
                )
            default_effort = effort_value

    intended_migration = UNSET
    migration_target: Graph | None = None
    if "intended_migration" in request.model_fields_set:
        patch = request.intended_migration
        if patch is None:
            intended_migration = None
        else:
            to_graph = patch.to_graph.strip()
            if not to_graph:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="to_graph must not be blank"
                )
            node_name = patch.node.strip() if patch.node is not None else None
            if patch.node is not None and not node_name:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="node must not be blank")
            migration_target = _resolve_graph_by_id_or_name(services, to_graph)
            if migration_target is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {to_graph}")
            mode = MigrationMode.FORCED if node_name is not None else MigrationMode.AUTO
            intended_migration = IntendedMigration(mode=mode, graph_id=migration_target.graph_id, node_name=node_name)

    edit = ChunkEdit(
        graph_id=graph_id,
        default_model=default_model,
        default_effort=default_effort,
        intended_migration=intended_migration,
    )
    try:
        services.edit.edit(chunk, edit, graph_target=graph_target, migration_target=migration_target)
    except ChunkNotEditable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ChunkAlreadyMoved as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except TargetGraphRetired as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except MigrationTargetIsCurrentPin as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ForcedNodeUnknown as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    updated = services.chunks.get(chunk_id)
    assert updated is not None, "the chunk existed a moment ago and this edit does not delete chunks"
    chunk_events.publish_chunk_changed(services, chunk_id, cause="edited", prev_status=prev_status)
    return ChunkPatchResponse(
        chunk_id=chunk_id,
        graph_id=updated.graph_id,
        default_model=list(updated.default_model),
        default_effort=updated.default_effort,
        intended_migration=_intended_migration_view(services, updated),
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
