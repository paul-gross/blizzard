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

from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.artifacts import ArtifactRow, GitCommitArtifact, from_row, store_key
from blizzard.hub.domain.envelope import build_node_envelope
from blizzard.hub.domain.ingest import IngestConflict
from blizzard.hub.domain.work import (
    Chunk,
    ChunkFacts,
    PmPointer,
    current_node_id,
    derive_chunk_status,
    latest_epoch,
    open_escalation,
    transition_history,
)
from blizzard.hub.pm.source import PmSourceError
from blizzard.wire.chunk import (
    ArtifactView,
    ChunkDetail,
    ChunkIngestConflict,
    ChunkIngestRequest,
    ChunkIngestResponse,
    ChunkSummary,
    EscalationView,
    PmItemView,
    PmPointerModel,
    RouteView,
    TransitionView,
)
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope
from blizzard.wire.facts import EscalationReport, LeaseMintReport

router = APIRouter(prefix="/api", tags=["chunks"])


def _pointer_models(chunk: Chunk) -> list[PmPointerModel]:
    return [PmPointerModel(provider=p.provider, url=p.url) for p in chunk.pm_pointers]


def _history_views(facts: ChunkFacts) -> list[TransitionView]:
    """The chunk's transitions oldest-first — the board's node-history timeline (D-036)."""
    return [
        TransitionView(
            from_node_id=t.from_node_id,
            to_node_id=t.to_node_id,
            choice_name=t.choice_name,
            epoch=t.epoch,
            recorded_at=t.recorded_at.isoformat(),
        )
        for t in transition_history(facts)
    ]


def _artifact_views(rows: list[ArtifactRow]) -> list[ArtifactView]:
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
            views.append(
                ArtifactView(
                    **common,
                    repo=artifact.repo,
                    branch_name=artifact.branch_name,
                    commit_hash=artifact.commit_hash,
                )
            )
        else:
            views.append(ArtifactView(**common, content=artifact.content))
    return views


def _current_node(services: HubServices, chunk: Chunk, facts: ChunkFacts, cache: dict[str, str | None]) -> str | None:
    """The chunk's current node id — the newest transition's target, or the pinned
    graph's entry node before the first transition (a nicer board value than ``None``);
    the entry node per graph is memoised in ``cache`` so a fleet list resolves once."""
    resolved = current_node_id(facts)
    if resolved is not None:
        return resolved
    if chunk.graph_id not in cache:
        graph = services.graphs.get(chunk.graph_id)
        cache[chunk.graph_id] = graph.entry_node_id if graph is not None else None
    return cache[chunk.graph_id]


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
    services.events.publish_chunk_changed(chunk_id, "ready")
    return ChunkIngestResponse(chunk_id=chunk_id)


@router.get("/chunks", response_model=list[ChunkSummary])
def list_chunks(services: Annotated[HubServices, Depends(get_services)]) -> list[ChunkSummary]:
    """The fleet chunk list — derived status per chunk (D-004)."""
    summaries: list[ChunkSummary] = []
    entry_cache: dict[str, str | None] = {}
    for chunk in services.chunks.list_all():
        facts = services.chunks.load_facts(chunk.chunk_id) or ChunkFacts(minted=True)
        summaries.append(
            ChunkSummary(
                chunk_id=chunk.chunk_id,
                graph_id=chunk.graph_id,
                status=derive_chunk_status(facts),
                current_node_id=_current_node(services, chunk, facts, entry_cache),
                pm_pointers=_pointer_models(chunk),
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
    return ChunkDetail(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        status=derive_chunk_status(facts),
        current_node_id=_current_node(services, chunk, facts, {}),
        latest_epoch=latest_epoch(facts),
        pm_pointers=_pointer_models(chunk),
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
        history=_history_views(facts),
        artifacts=_artifact_views(services.chunks.load_artifacts(chunk_id)),
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
    return response


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


@router.get("/chunks/{chunk_id}/pm-item", response_model=PmItemView)
def get_pm_item(chunk_id: str, services: Annotated[HubServices, Depends(get_services)]) -> PmItemView:
    """Pass-through PM item read — body + comments, contents never stored (D-047)."""
    if services.pm_source is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="no PM work-source is configured")
    chunk = services.chunks.get(chunk_id)
    if chunk is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown chunk {chunk_id}")
    if not chunk.pm_pointers:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="chunk has no PM pointer")
    pointer = chunk.pm_pointers[0]
    try:
        item = services.pm_source.fetch(pointer)
    except PmSourceError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return PmItemView(
        provider=pointer.provider,
        url=pointer.url,
        fetched_at=services.clock.now().isoformat(),
        body=item.body,
        comments=item.comments,
    )
