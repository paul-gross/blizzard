"""Chunk routes — ingest, list, detail, envelope, completion, PM pass-through.

The chunk-facing surface of the hub API (D-024/D-047). Controllers stay read-only
over the store (``bzh:controller-read-only``): ingest and completion delegate to
domain services that hold the write repository; the list/detail/envelope reads
derive status and current node from facts (``bzh:facts-not-status``), never a stored
column. The PM read is a vendor-native pass-through whose contents are never stored
(D-047).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.decisions import to_decision_view
from blizzard.hub.api.deps import get_services
from blizzard.hub.api.questions import question_view
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.artifacts import ArtifactRow, GitCommitArtifact, from_row, store_key
from blizzard.hub.domain.decisions import NotEscalated
from blizzard.hub.domain.detach import NotRouted
from blizzard.hub.domain.envelope import build_node_envelope
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    ChunkStatus,
    PmPointer,
    awaiting_external_merge,
    current_node_id,
    derive_chunk_status,
    latest_epoch,
    open_escalation,
    transition_history,
)
from blizzard.hub.pm.label import ForgeWebBase, forge_web_base, pointer_label
from blizzard.hub.pm.source import PmSourceError
from blizzard.wire.chunk import (
    ArtifactView,
    CheckDeliveryResponse,
    ChunkDetail,
    ChunkIngestConflict,
    ChunkIngestRequest,
    ChunkIngestResponse,
    ChunkSummary,
    EscalationView,
    PmItemEntry,
    PmItemsView,
    PmPointerView,
    PrView,
    RouteView,
    TransitionView,
)
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.decision import DecisionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import EscalationReport, LeaseMintReport

router = APIRouter(prefix="/api", tags=["chunks"])


def _pointer_views(chunk: Chunk) -> list[PmPointerView]:
    """Each pointer with its board-legible label (D-075) — null when not issue-shaped."""
    return [PmPointerView(provider=p.provider, url=p.url, label=pointer_label(p)) for p in chunk.pm_pointers]


def _publish_open_decision(services: HubServices, chunk_id: str) -> None:
    """Emit ``decision-opened`` if the chunk now carries a live, unresolved gate (D-045)."""
    decision = services.chunks.decision_for_chunk(chunk_id)
    if decision is not None and not decision.resolved and not decision.transitioned:
        services.events.publish_decision_opened(chunk_id, decision.decision_id)


def _node_name(graph: Graph | None, node_id: str | None) -> str | None:
    """The human graph name for ``node_id`` in ``graph``, or ``None`` when unresolvable."""
    if graph is None or node_id is None:
        return None
    node = graph.node_by_id(node_id)
    return node.name if node is not None else None


def _history_views(facts: ChunkFacts, graph: Graph | None) -> list[TransitionView]:
    """The chunk's transitions oldest-first — the board's node-history timeline (D-036).

    Each edge's node ids are resolved to their human graph names against the chunk's
    pinned graph so the timeline reads ``build -> review`` (D-075)."""
    return [
        TransitionView(
            from_node_id=t.from_node_id,
            from_node_name=_node_name(graph, t.from_node_id),
            to_node_id=t.to_node_id,
            to_node_name=_node_name(graph, t.to_node_id),
            choice_name=t.choice_name,
            epoch=t.epoch,
            recorded_at=iso_utc(t.recorded_at),
        )
        for t in transition_history(facts)
    ]


def _artifact_views(rows: list[ArtifactRow], web_base: ForgeWebBase | None) -> list[ArtifactView]:
    """The chunk's inline artifact store — every entry, with an asset's content and a
    git-commit's pinned reference surfaced (D-036); ordered by ``{node}.{name}.{epoch}``
    so a re-run's later-epoch entry follows its predecessors (append-only history)."""
    views: list[ArtifactView] = []
    for row in sorted(rows, key=lambda r: (r.node_name, r.name, r.epoch)):
        artifact = from_row(row)
        common = {
            "key": store_key(row),
            "kind": row.kind.value,
            "name": row.name,
            "node_id": row.node_id,
            "node_name": row.node_name,
            "epoch": row.epoch,
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
    legible without reassembly (D-075); the graph per graph_id is memoised in ``cache``
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
    """Ingest by pointer (D-047); 409 on a pointer held by a live chunk (D-093)."""
    if not request.pointers:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="at least one pointer required")
    graph = services.graph_mint.ensure_default(services.default_graph_doc, definition_yaml=services.default_graph_yaml)
    pointers = [PmPointer(provider=p.provider, url=p.url) for p in request.pointers]
    try:
        chunk_id = services.ingest.ingest(pointers, graph=graph)
    except IngestConflict as exc:
        conflict = ChunkIngestConflict(
            existing_chunk_id=exc.existing_chunk_id, provider=exc.pointer.provider, url=exc.pointer.url
        )
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=conflict.model_dump())
    # A freshly ingested chunk rests ``not_ready`` (D-103) — visible on the board but not
    # in the ready queue, so no ``queue-changed`` fires until it is promoted.
    services.events.publish_chunk_changed(chunk_id, ChunkStatus.NOT_READY.value)
    return ChunkIngestResponse(chunk_id=chunk_id)


@router.get("/chunks", response_model=list[ChunkSummary])
def list_chunks(services: Annotated[HubServices, Depends(get_services)]) -> list[ChunkSummary]:
    """The fleet chunk list — derived status per chunk (D-004)."""
    summaries: list[ChunkSummary] = []
    graph_cache: dict[str, Graph | None] = {}
    for chunk in services.chunks.list_all():
        facts = services.chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
        node_id, node_name = _current_node(services, chunk, facts, graph_cache)
        summaries.append(
            ChunkSummary(
                chunk_id=chunk.chunk_id,
                graph_id=chunk.graph_id,
                status=derive_chunk_status(facts),
                current_node_id=node_id,
                current_node_name=node_name,
                pm_pointers=_pointer_views(chunk),
            )
        )
    return summaries


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> ChunkDetail:
    """One chunk aggregate in full — derived status, current node, route (D-036)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    route = services.chunks.route_of(chunk_id)
    escalation = open_escalation(facts)
    decision = services.chunks.decision_for_chunk(chunk_id)
    graph = services.graphs.get(chunk.graph_id)
    node_id = current_node_id(facts) or (graph.entry_node_id if graph is not None else None)
    node_name = _node_name(graph, node_id)
    web_base = forge_web_base(p.url for p in chunk.pm_pointers)
    return ChunkDetail(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        status=derive_chunk_status(facts),
        current_node_id=node_id,
        current_node_name=node_name,
        latest_epoch=latest_epoch(facts),
        pm_pointers=_pointer_views(chunk),
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
        decision=to_decision_view(decision) if decision is not None else None,
        history=_history_views(facts, graph),
        artifacts=_artifact_views(services.chunks.load_artifacts(chunk_id), web_base),
        questions=[question_view(q) for q in services.chunks.load_questions(chunk_id) if not q.answered],
        awaiting_external_merge=awaiting_external_merge(facts),
        open_prs=[PrView(repo=pr.repo, number=pr.number, url=pr.url) for pr in facts.pr_opened],
    )


@router.get("/chunks/{chunk_id}/envelope", response_model=NodeEnvelope)
def get_envelope(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> NodeEnvelope:
    """The chunk's current node envelope, idempotent — the lost-apply re-read (D-090)."""
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
    )


@router.post("/chunks/{chunk_id}/completions", response_model=ApplyResponse)
def submit_completion(
    chunk_id: str,
    submission: CompletionSubmission,
    services: Annotated[HubServices, Depends(get_services)],
) -> ApplyResponse:
    """Apply a node-step's completion atomically; reply carries the next envelope (D-072)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    response = services.apply.apply(chunk, graph, submission)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    # A completion landing on a human-judged node opens a graph gate (D-045): surface it.
    _publish_open_decision(services, chunk_id)
    return response


@router.post("/chunks/{chunk_id}/check-delivery", response_model=CheckDeliveryResponse)
def check_delivery(
    chunk_id: str,
    services: Annotated[HubServices, Depends(get_services)],
) -> CheckDeliveryResponse:
    """Poll a parked open-pr chunk's PRs; finalize the delivery once all are terminal (D-065).

    The on-demand external-merge detection (the impatient path): for a chunk parked in
    ``open-pr`` mode, check every open PR through the forge and, when all have merged or
    closed, write the terminal facts so the chunk flips to ``done`` and its environments
    release. A no-op when the chunk has no open PR or is already finalized.
    """
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    result = services.delivery_check.check(chunk, graph)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    derived = derive_chunk_status(facts)
    services.events.publish_chunk_changed(chunk_id, derived.value)
    return CheckDeliveryResponse(
        chunk_id=chunk_id,
        status=derived,
        finalized=result.finalized,
        open_prs=result.open_prs,
        detail=result.detail,
    )


@router.post("/chunks/{chunk_id}/decisions", response_model=ApplyResponse)
def submit_decision(
    chunk_id: str,
    submission: DecisionSubmission,
    services: Annotated[HubServices, Depends(get_services)],
) -> ApplyResponse:
    """Runner-config gate: park the chunk on a decision in place of a transition (D-032/D-045)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    graph = services.graphs.get(chunk.graph_id)
    if graph is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="chunk's pinned graph is missing")
    response = services.decisions.submit(chunk, graph, submission)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    # The runner-config gate parked the chunk on an open decision (D-032): surface it.
    _publish_open_decision(services, chunk_id)
    return response


@router.post("/chunks/{chunk_id}/requeues", status_code=status.HTTP_202_ACCEPTED)
def requeue_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> dict[str, str]:
    """Close an escalation by supersession: requeue at the current node (D-067)."""
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    try:
        services.requeue.requeue(chunk_id)
    except NotEscalated as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    services.events.publish_queue_changed()  # requeue can re-admit the chunk to the queue (D-067)
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/detach", status_code=status.HTTP_202_ACCEPTED)
def detach_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> dict[str, str]:
    """Forcibly release a chunk from its runner without touching any escalation (D-088)."""
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    try:
        services.detach.detach(chunk)
    except NotRouted as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    services.events.publish_queue_changed()  # a detached chunk re-enters the ready queue (D-088)
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/promote", status_code=status.HTTP_202_ACCEPTED)
def promote_chunk(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> dict[str, str]:
    """Promote a not-ready chunk to ready so a runner may claim it (D-103).

    Idempotent: promoting an already-ready or already-running chunk is a harmless no-op.
    404 only when the chunk is unknown."""
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    services.promote.promote(chunk_id)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    services.events.publish_queue_changed()  # a promoted chunk enters the ready queue (D-048/D-103)
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/leases", status_code=status.HTTP_202_ACCEPTED)
def report_lease(
    chunk_id: str,
    report: LeaseMintReport,
    services: Annotated[HubServices, Depends(get_services)],
) -> dict[str, str]:
    """Land a runner's ``lease.minted`` — keeps the epoch fence in lockstep (D-044)."""
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    services.runner_facts.record_lease_minted(chunk_id, epoch=report.epoch, runner_id=report.runner_id)
    return {"chunk_id": chunk_id}


@router.post("/chunks/{chunk_id}/escalations", status_code=status.HTTP_202_ACCEPTED)
def report_escalation(
    chunk_id: str,
    report: EscalationReport,
    services: Annotated[HubServices, Depends(get_services)],
) -> dict[str, str]:
    """Land a runner's ``escalation.recorded`` — the chunk derives ``needs_human`` (D-009)."""
    if services.chunks.get(chunk_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    services.runner_facts.record_escalation(chunk_id, epoch=report.epoch, takeover_command=report.takeover_command)
    facts = services.chunks.load_facts(chunk_id) or ChunkFacts(minted=True)
    services.events.publish_chunk_changed(chunk_id, derive_chunk_status(facts).value)
    return {"chunk_id": chunk_id}


@router.get("/chunks/{chunk_id}/pm-items", response_model=PmItemsView)
def get_pm_items(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> PmItemsView:
    """Pass-through PM items read (D-047/D-084) — one entry per pointer, contents never stored.

    Each pointer is fetched fresh from the forge; a per-pointer forge failure degrades to an
    ``error`` on that entry rather than failing the whole read, so a grouped chunk (D-047) still
    surfaces the pointers it reached beside a notice for the ones it did not. A chunk with no
    pointers is an empty list — the board's empty state — not a 404."""
    if services.pm_source is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no PM work-source is configured")
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    fetched_at = iso_utc(services.clock.now())
    entries: list[PmItemEntry] = []
    for pointer in chunk.pm_pointers:
        label = pointer_label(pointer)
        try:
            item = services.pm_source.fetch(pointer)
        except PmSourceError as exc:
            entries.append(
                PmItemEntry(
                    provider=pointer.provider, url=pointer.url, label=label, fetched_at=fetched_at, error=str(exc)
                )
            )
        else:
            entries.append(
                PmItemEntry(
                    provider=pointer.provider,
                    url=pointer.url,
                    label=label,
                    fetched_at=fetched_at,
                    title=item.title,
                    body=item.body,
                    comments=item.comments,
                )
            )
    return PmItemsView(items=entries)
