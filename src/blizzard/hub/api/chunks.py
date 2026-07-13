"""Chunk routes — ingest, list, detail, envelope, completion, PM pass-through.

The chunk-facing surface of the hub API (D-024/D-047). All bodies are 501 stubs;
the P6 hub-track builder wires each to the read/write chunk repositories
(:mod:`blizzard.hub.domain.work`) and the derivation queries. Controllers stay
read-only over the store (``bzh:controller-read-only``): ingest and completion
flow through a domain service that holds the write repository — the edge resolves
inputs and derives status, it never mutates around the domain.

Status on every view is **derived** (:func:`~blizzard.hub.domain.work.derive_chunk_status`),
never a stored column (``bzh:facts-not-status``).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from blizzard.wire.chunk import (
    ChunkDetail,
    ChunkIngestRequest,
    ChunkIngestResponse,
    ChunkSummary,
    PmItemView,
)
from blizzard.wire.completion import CompletionSubmission
from blizzard.wire.envelope import ApplyResponse, NodeEnvelope

router = APIRouter(prefix="/api", tags=["chunks"])

_NOT_IMPLEMENTED = "chunk lifecycle lands in the P6 walking skeleton"


def _stub() -> HTTPException:
    return HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)


@router.post("/chunks", response_model=ChunkIngestResponse, status_code=status.HTTP_201_CREATED)
def ingest_chunk(request: ChunkIngestRequest) -> ChunkIngestResponse:
    """Ingest by pointer (D-047); 409 on a pointer held by a live chunk (D-093)."""
    raise _stub()


@router.get("/chunks", response_model=list[ChunkSummary])
def list_chunks() -> list[ChunkSummary]:
    """The fleet chunk list — derived status per chunk (D-004)."""
    raise _stub()


@router.get("/chunks/{chunk_id}", response_model=ChunkDetail)
def get_chunk(chunk_id: str) -> ChunkDetail:
    """One chunk aggregate in full — derived status, current node, route (D-036)."""
    raise _stub()


@router.get("/chunks/{chunk_id}/envelope", response_model=NodeEnvelope)
def get_envelope(chunk_id: str) -> NodeEnvelope:
    """The chunk's current node envelope, idempotent — the lost-apply re-read (D-090)."""
    raise _stub()


@router.post("/chunks/{chunk_id}/completions", response_model=ApplyResponse)
def submit_completion(chunk_id: str, submission: CompletionSubmission) -> ApplyResponse:
    """Apply a node-step's completion atomically; reply carries the next envelope (D-072)."""
    raise _stub()


@router.get("/chunks/{chunk_id}/pm-item", response_model=PmItemView)
def get_pm_item(chunk_id: str) -> PmItemView:
    """Pass-through PM item read — body + comments, contents never stored (D-047)."""
    raise _stub()
