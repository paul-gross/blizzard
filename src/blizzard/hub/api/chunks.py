"""Chunk routes — ingest, list, detail, PM pass-through — the anonymous **operator**
surface (issue #87).

The chunk-facing surface of the hub API. Controllers stay read-only
over the store (``bzh:controller-read-only``): ingest delegates to
domain services that hold the write repository; the list/detail reads
derive status and current node from facts (``bzh:facts-not-status``), never a stored
column. The PM read is a vendor-native pass-through whose contents are never stored.
``POST /chunks/{id}/graph`` and ``POST /chunks/{id}/model`` (issue #27) repin a
not-ready chunk's workflow graph or model selection — read is already carried on the
list/detail views' ``graph_id``/``model`` fields; write is refused (409) once the chunk
has left ``not_ready``.

The envelope read, the completion/decision/lease/escalation writes, and ``hub-advance``
(#65/#66, driven by the runner's own ADVANCE poll) moved to the runner-authenticated
fleet router (:mod:`blizzard.hub.api.fleet`, issue #87) — no board or CLI caller ever
reached any of them. ``get_chunk`` and ``get_pm_items`` stay here (the board's own
reads) *and* gain fleet-side counterparts, since the runner reads both too;
``dependencies=[Depends(reject_runner_principal)]`` on this router rejects a runner's
bearer token here rather than treating it as anonymous-plus-credential — a runner's
token is confined to the fleet router.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.foundation.ids import minted_at
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.decisions import to_decision_view
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.questions import question_view
from blizzard.hub.composition import HubServices
from blizzard.hub.delivery.hub_node import poll_interval_for
from blizzard.hub.domain.artifacts import ArtifactRow, GitCommitArtifact, from_row, store_key
from blizzard.hub.domain.decisions import NotEscalated
from blizzard.hub.domain.detach import NotRouted
from blizzard.hub.domain.edit import ChunkNotEditable
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.pause import ChunkNotPausable
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    ChunkStatus,
    PmPointer,
    awaiting_external_merge,
    current_node_id,
    derive_chunk_status,
    derive_chunk_usage,
    has_landed_repos,
    hub_node_pending,
    latest_epoch,
    open_escalation,
    open_pause,
    transition_history,
)
from blizzard.hub.pm.source import IPmSource, IPmSourceRegistry, PmSourceError
from blizzard.wire.chunk import (
    ArtifactView,
    BounceView,
    ChunkDetail,
    ChunkGraphUpdateRequest,
    ChunkGraphView,
    ChunkIngestConflict,
    ChunkIngestRequest,
    ChunkIngestResponse,
    ChunkModelUpdateRequest,
    ChunkModelView,
    ChunkPauseRequest,
    ChunkSummary,
    ChunkUsageTotalView,
    ChunkUsageView,
    EscalationView,
    HubMarkerRequest,
    HubMarkerResponse,
    MigrationView,
    PauseView,
    PendingView,
    PmItemEntry,
    PmItemsView,
    PmPointerView,
    PrView,
    RouteView,
    TransitionView,
)

router = APIRouter(prefix="/api", tags=["chunks"], dependencies=[Depends(reject_runner_principal)])


def _pointer_views(chunk: Chunk, pm: IPmSourceRegistry) -> list[PmPointerView]:
    """Each pointer with its board-legible label and browser URL —
    both null when no configured source names ``pointer.source``.

    Each pointer is resolved to its own binding by name (``pm.get(p.source)``) — a
    chunk's pointers need not all share one source."""
    views: list[PmPointerView] = []
    for p in chunk.pm_pointers:
        source = pm.get(p.source)
        views.append(
            PmPointerView(
                source=p.source,
                ref=p.ref,
                label=source.label(p) if source is not None else None,
                web_url=source.web_url(p) if source is not None else None,
            )
        )
    return views


def publish_open_decision(services: HubServices, chunk_id: str) -> None:
    """Emit ``decision-opened`` if the chunk now carries a live, unresolved gate.

    Public (issue #87): the fleet router's completion/decision writes
    (:mod:`blizzard.hub.api.fleet`) call this the same way this module's own
    ``get_chunk``/``to_decision_view`` reuse crosses the chunks/decisions/questions
    module boundary."""
    decision = services.chunks.decision_for_chunk(chunk_id)
    if decision is not None and not decision.resolved and not decision.transitioned:
        services.events.publish_decision_opened(chunk_id, decision.decision_id)


def _node_name(graph: Graph | None, node_id: str | None) -> str | None:
    """The human graph name for ``node_id`` in ``graph``, or ``None`` when unresolvable."""
    if graph is None or node_id is None:
        return None
    node = graph.node_by_id(node_id)
    return node.name if node is not None else None


def _graph_name(graph: Graph | None) -> str | None:
    return graph.name if graph is not None else None


def _history_views(facts: ChunkFacts, graphs: dict[str | None, Graph | None]) -> list[TransitionView]:
    """The chunk's transitions oldest-first — the board's node-history timeline.

    Each edge's node ids are resolved to their human graph names against *the graph the
    transition happened in* (issue #90), keyed by ``TransitionFact.graph_id`` — not the
    chunk's current pin. A single-graph history keys every step to the one graph exactly
    as before; a chunk that later migrates still reads its old-graph steps' names, rather
    than degrading them to raw ``nd_`` ids against the new pin. The step's own
    ``graph_id``/``graph_name`` ride along so the board can label which graph it belongs to."""
    views: list[TransitionView] = []
    for t in transition_history(facts):
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

    Each step names the graph it left and the graph it re-pinned to: the ``from_node`` is
    resolved against the ``from_graph``, the ``landed_node`` against the ``to_graph`` — each
    side's own graph, so neither degrades to a raw id when the two differ. The board weaves
    these into the timeline alongside :func:`_history_views` by ``recorded_at``."""
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
                recorded_at=iso_utc(m.recorded_at),
            )
        )
    return views


def _history_graphs(services: HubServices, chunk: Chunk, facts: ChunkFacts) -> dict[str | None, Graph | None]:
    """The graphs a chunk's history spans, by id (issue #90).

    The chunk's current pin plus every distinct graph its transitions were recorded in and
    every graph its migrations left or entered (only ever the one, until a cross-graph
    migration lands) — each resolved once so :func:`_history_views` /
    :func:`_migration_views` can name nodes against their own graph."""
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
    usage = derive_chunk_usage(facts)
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


def _branch_url_source(chunk: Chunk, pm: IPmSourceRegistry) -> IPmSource | None:
    """The binding a chunk's artifact branch links resolve through.

    The one-forge-per-chunk assumption is no longer *inferred* by sniffing
    whichever pointer URL happened to parse first — it is *declared*: the chunk's
    first pointer whose ``source`` names a configured binding lends its
    :meth:`~blizzard.hub.pm.source.IPmSource.branch_url`. ``None`` when no pointer's
    source is configured — the degradation ``_artifact_views`` already preserves."""
    for p in chunk.pm_pointers:
        source = pm.get(p.source)
        if source is not None:
            return source
    return None


def _artifact_views(rows: list[ArtifactRow], web_base: IPmSource | None) -> list[ArtifactView]:
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
    node_id = current_node_id(facts) or (graph.entry_node_id if graph is not None else None)
    if node_id is None:
        return None, None
    node = graph.node_by_id(node_id) if graph is not None else None
    return node_id, node.name if node is not None else None


@router.post("/chunks", response_model=ChunkIngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_chunk(request: ChunkIngestRequest, services: Annotated[HubServices, Depends(get_services)]) -> object:
    """Ingest by source-native token; 422 on a token no configured source
    claims; 409 on a pointer held by a live chunk."""
    if not request.tokens:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="at least one token required")
    # Resolution before minting, and before the live-holder check: an unresolvable
    # token should not consult the store, and the whole request rejects together
    # rather than partially ingesting. The route resolves; the domain stays
    # registry-free (bzh:domain-takes-objects) — it never sees the registry at all.
    pointers: list[PmPointer] = []
    for token in request.tokens:
        pointer = services.pm.resolve(token)
        if pointer is None:
            configured = ", ".join(sorted(services.pm.names())) or "none"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(f"token {token!r} is not claimed by any configured PM source (configured: {configured})"),
            )
        pointers.append(pointer)
    graph = services.graph_mint.ensure_default(services.default_graph_doc, definition_yaml=services.default_graph_yaml)
    try:
        chunk_id = services.ingest.ingest(pointers, graph=graph)
    except IngestConflict as exc:
        conflict = ChunkIngestConflict(
            existing_chunk_id=exc.existing_chunk_id, source=exc.pointer.source, ref=exc.pointer.ref
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    # A freshly ingested chunk rests ``not_ready`` — visible on the board but not
    # in the ready queue, so no ``queue-changed`` fires until it is promoted.
    services.events.publish_chunk_changed(chunk_id, ChunkStatus.NOT_READY.value)
    return ChunkIngestResponse(chunk_id=chunk_id)


@router.get("/chunks", response_model=list[ChunkSummary])
def list_chunks(services: Annotated[HubServices, Depends(get_services)]) -> list[ChunkSummary]:
    """The fleet chunk list — derived status per chunk."""
    summaries: list[ChunkSummary] = []
    graph_cache: dict[str, Graph | None] = {}
    for chunk in services.chunks.list_all():
        facts = services.chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
        node_id, node_name = _current_node(services, chunk, facts, graph_cache)
        route = services.chunks.route_of(chunk.chunk_id)
        summaries.append(
            ChunkSummary(
                chunk_id=chunk.chunk_id,
                graph_id=chunk.graph_id,
                status=derive_chunk_status(facts),
                current_node_id=node_id,
                current_node_name=node_name,
                pm_pointers=_pointer_views(chunk, services.pm),
                model=chunk.model,
                runner_id=route.runner_id if route is not None else None,
                cost=_usage_total_view(facts),
            )
        )
    return summaries


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkDetail:
    """One chunk aggregate in full — derived status, current node, route."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    route = services.chunks.route_of(chunk_id)
    escalation = open_escalation(facts)
    pause = open_pause(facts)
    decision = services.chunks.decision_for_chunk(chunk_id)
    graph = services.graphs.get(chunk.graph_id)
    node_id = current_node_id(facts) or (graph.entry_node_id if graph is not None else None)
    node_name = _node_name(graph, node_id)
    web_base = _branch_url_source(chunk, services.pm)
    history_graphs = _history_graphs(services, chunk, facts)
    artifacts = services.chunks.load_artifacts(chunk_id)
    pending = hub_node_pending(facts)
    pending_view = None
    if pending is not None:
        pending_node = graph.node_by_id(pending.node_id) if graph is not None else None
        if pending_node is not None:
            next_poll_at = pending.polled_at + poll_interval_for(pending_node)
            pending_view = PendingView(node_name=pending_node.name, next_poll_at=iso_utc(next_poll_at))
    return ChunkDetail(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        status=derive_chunk_status(facts),
        current_node_id=node_id,
        current_node_name=node_name,
        latest_epoch=latest_epoch(facts),
        pm_pointers=_pointer_views(chunk, services.pm),
        model=chunk.model,
        route=RouteView(
            runner_id=route.runner_id,
            workspace_id=route.workspace_id,
            environment_ids=route.environment_ids,
        )
        if route is not None
        else None,
        escalation=EscalationView(epoch=escalation.epoch, takeover_command=escalation.takeover_command)
        if escalation is not None
        else None,
        pause=PauseView(by=pause.set_by, set_at=iso_utc(pause.set_at)) if pause is not None else None,
        decision=to_decision_view(decision) if decision is not None else None,
        history=_history_views(facts, history_graphs),
        migrations=_migration_views(facts, history_graphs),
        artifacts=_artifact_views(artifacts, web_base),
        questions=[question_view(q) for q in services.chunks.load_questions(chunk_id) if not q.answered],
        awaiting_external_merge=awaiting_external_merge(facts),
        open_prs=[PrView(repo=pr.repo, number=pr.number, url=pr.url) for pr in facts.pr_opened],
        cost=_usage_total_view(facts),
        usage=_usage_history_views(facts),
        pending=pending_view,
        landed=has_landed_repos(facts, artifacts),
        bounces=[
            BounceView(cause=b.cause, envelope=b.envelope, recorded_at=iso_utc(b.recorded_at))
            for b in sorted(facts.bounces, key=lambda b: b.recorded_at)
        ],
    )


@router.post("/chunks/{chunk_id}/hub-markers", response_model=HubMarkerResponse)
def record_hub_marker(
    chunk_id: str,
    node_id: str,
    epoch: int,
    request_body: HubMarkerRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> HubMarkerResponse:
    """The mid-run marker callback (#65) — a ``run:`` step's own dynamic-loop marker.

    Mirrors ``blizzard runner ask``'s worker-facing callback shape: a hub command
    node's script POSTs here (via the injected
    ``BZ_HUB_MARKER_CALLBACK_URL``, which already carries ``node_id``/``epoch``) to
    record a marker artifact mid-run, ahead of that command's own exit — enabling a
    dynamic loop (``merge repo -> push -> record merged/<repo> -> next``). Idempotent
    per ``(chunk, node, name, epoch)``, exactly like the ``produces:`` marker the
    executor records on a step's own exit.
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


@router.post("/chunks/{chunk_id}/requeues", status_code=status.HTTP_202_ACCEPTED)
def requeue_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> dict[str, str]:
    """Close an escalation by supersession: requeue at the current node."""
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    try:
        services.requeue.requeue(chunk_id)
    except NotEscalated as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    services.events.publish_queue_changed()  # requeue can re-admit the chunk to the queue
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/detach", status_code=status.HTTP_202_ACCEPTED)
def detach_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> dict[str, str]:
    """Forcibly release a chunk from its runner without touching any escalation."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    try:
        services.detach.detach(chunk)
    except NotRouted as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    services.events.publish_queue_changed()  # a detached chunk re-enters the ready queue
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/pause", status_code=status.HTTP_202_ACCEPTED)
def pause_chunk(
    chunk_id: str, request: ChunkPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> dict[str, str]:
    """Set a chunk's operator pause brake — the claim is kept, unlike detach (issue #46)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    try:
        services.pause.pause(chunk, by=request.by)
    except ChunkNotPausable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    services.events.publish_queue_changed()  # a pause moves the chunk out of the ready queue (issue #46)
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/resume", status_code=status.HTTP_202_ACCEPTED)
def resume_chunk(
    chunk_id: str, request: ChunkPauseRequest, services: Annotated[HubServices, Depends(get_services)]
) -> dict[str, str]:
    """Clear a chunk's operator pause brake — idempotent, never refused (issue #46)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    services.pause.resume(chunk, by=request.by)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    services.events.publish_queue_changed()  # a resume can re-admit the chunk to the queue (issue #46)
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/promote", status_code=status.HTTP_202_ACCEPTED)
def promote_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> dict[str, str]:
    """Promote a not-ready chunk to ready so a runner may claim it.

    Idempotent: promoting an already-ready or already-running chunk is a harmless no-op.
    404 only when the chunk is unknown."""
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    services.promote.promote(chunk_id)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    services.events.publish_queue_changed()  # a promoted chunk enters the ready queue
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/graph", response_model=ChunkGraphView, status_code=status.HTTP_202_ACCEPTED)
def set_chunk_graph(
    chunk_id: str, request: ChunkGraphUpdateRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkGraphView:
    """Repin a not-ready chunk's workflow graph (issue #27).

    404 on an unknown chunk or an unknown target graph; 409 once the chunk has left
    ``not_ready`` (already promoted, claimed, running, or later)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(request.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown graph {request.graph_id}")
    try:
        services.edit.set_graph(chunk, graph=graph)
    except ChunkNotEditable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    return ChunkGraphView(chunk_id=chunk_id, graph_id=graph.graph_id)


@router.post("/chunks/{chunk_id}/model", response_model=ChunkModelView, status_code=status.HTTP_202_ACCEPTED)
def set_chunk_model(
    chunk_id: str, request: ChunkModelUpdateRequest, services: Annotated[HubServices, Depends(get_services)]
) -> ChunkModelView:
    """Repin a not-ready chunk's model selection (issue #27).

    404 on an unknown chunk; 422 on a blank model; 409 once the chunk has left
    ``not_ready`` (already promoted, claimed, running, or later)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    model = request.model.strip()
    if not model:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="model must not be blank")
    try:
        services.edit.set_model(chunk, model=model)
    except ChunkNotEditable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    return ChunkModelView(chunk_id=chunk_id, model=model)


@router.get("/chunks/{chunk_id}/pm-items", response_model=PmItemsView)
def get_pm_items(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> PmItemsView:
    """Pass-through PM items read — one entry per pointer, contents never stored.

    Each pointer is resolved to its own binding by name (``pm.get(pointer.source)``), then
    fetched fresh from the forge; a per-pointer resolution or forge failure degrades to an
    ``error`` on that entry rather than failing the whole read, so a grouped chunk
    still surfaces the pointers it reached beside a notice for the ones it did not. A chunk
    with no pointers is an empty list — the board's empty state — not a 404. No configured
    PM source at all is a 503 up front — the request-wide degradation preserved unchanged
    from before per-pointer resolution existed."""
    if not services.pm.names():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no PM work-source is configured")
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    fetched_at = iso_utc(services.clock.now())
    entries: list[PmItemEntry] = []
    for pointer in chunk.pm_pointers:
        source = services.pm.get(pointer.source)
        if source is None:
            entries.append(
                PmItemEntry(
                    source=pointer.source,
                    ref=pointer.ref,
                    label=None,
                    web_url=None,
                    fetched_at=fetched_at,
                    error=f"no configured PM source named {pointer.source!r}",
                )
            )
            continue
        label = source.label(pointer)
        web_url = source.web_url(pointer)
        try:
            item = source.fetch(pointer)
        except PmSourceError as exc:
            entries.append(
                PmItemEntry(
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
                PmItemEntry(
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
    return PmItemsView(items=entries)
