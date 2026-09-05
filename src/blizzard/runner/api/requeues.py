"""``blizzard runner requeue`` — ``POST /chunks/{id}/requeues`` (issue #53).

Appends the fact that clears a chunk's local needs_human hold and returns immediately.
Read-only over its wiring (``bzh:controller-read-only``)."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.runner.api.chunk_scope import resolved_requeue_scope
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.domain.requeue import ChunkNotRequeueable, RequeueBlockedByOpenTakeover
from blizzard.wire.requeue import RequeueResponse

router = APIRouter(prefix="/api", tags=["runner"])


@router.post("/chunks/{chunk_id}/requeues", response_model=RequeueResponse, status_code=status.HTTP_202_ACCEPTED)
def requeue_chunk(chunk_id: str, request: Request) -> RequeueResponse:
    """Clear a needs_human chunk's local hold — the next FILL spawns a fresh attempt.

    ``409`` while the chunk's takeover is still open (end the interactive session first),
    or while the chunk carries no open escalation (nothing needs_human to clear)."""
    service = RunnerWiring.of(request).requeue()
    scope = resolved_requeue_scope(chunk_id, request)
    try:
        service.requeue(scope)
    except (RequeueBlockedByOpenTakeover, ChunkNotRequeueable) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return RequeueResponse(chunk_id=chunk_id, requeued=True)
