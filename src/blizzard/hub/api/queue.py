"""Queue routes — ``GET /api/queue/peek`` (D-080).

The read-only ready-queue peek a runner's FILL step does before claiming. 501 stub
— the P6 hub-track builder wires it to the read chunk repository's ready listing
(status derived: a ready chunk is minted with no live route — D-004).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from blizzard.wire.queue import QueuePeekResponse

router = APIRouter(prefix="/api", tags=["queue"])

_NOT_IMPLEMENTED = "queue peek lands in the P6 walking skeleton"


@router.get("/queue/peek", response_model=QueuePeekResponse)
def peek_queue() -> QueuePeekResponse:
    """The hub-ordered ready queue, read-only (D-080)."""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=_NOT_IMPLEMENTED)
