"""The runner-local chunk-detail pass-through proxy — read, pause, resume (issue #185).

Read-only over its wiring (``bzh:controller-read-only``): all three routes forward to the
hub URL the composition root resolved onto ``app.state.config``."""

from __future__ import annotations

from fastapi import APIRouter, Request, status

from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.wire.chunk import ChunkDetailView, ChunkSummary

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/chunks/{chunk_id}", response_model=ChunkDetailView)
def get_chunk(chunk_id: str, request: Request) -> ChunkDetailView:
    """Forward a chunk's detail read to the hub — the full aggregate minus ``escalation``
    (issue #314), including transition history and artifacts."""
    upstream = HubProxy.of(request, "chunk-detail").get(f"/api/fleet/chunks/{chunk_id}")
    return ChunkDetailView.model_validate(upstream.json())


@router.post("/chunks/{chunk_id}/pause", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def pause_chunk(chunk_id: str, request: Request) -> ChunkSummary:
    """Forward a chunk pause to the hub — kills the active worker, keeps the claim
    (issue #46). ``409`` when the chunk is not in a pausable state."""
    upstream = HubProxy.of(request, "chunk-detail").post(f"/api/fleet/chunks/{chunk_id}/pause")
    return ChunkSummary.model_validate(upstream.json())


@router.post("/chunks/{chunk_id}/resume", response_model=ChunkSummary, status_code=status.HTTP_202_ACCEPTED)
def resume_chunk(chunk_id: str, request: Request) -> ChunkSummary:
    """Forward a chunk resume to the hub — idempotent, never refused."""
    upstream = HubProxy.of(request, "chunk-detail").post(f"/api/fleet/chunks/{chunk_id}/resume")
    return ChunkSummary.model_validate(upstream.json())
