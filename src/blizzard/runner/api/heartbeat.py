"""The runner-local heartbeat endpoint — ``POST /api/heartbeat``.

Posted with the lease id the caller inherited from its spawn environment
(``BLIZZARD_LEASE_ID``); the daemon appends a heartbeat fact to its store and is the
only writer of that file. Read-only over its wiring (``bzh:controller-read-only``): an
unwired store answers 503 rather than pretending, which only the store-free app hits."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.foundation.clock import IClock
from blizzard.runner.store.repository import IWriteRunnerStore

router = APIRouter(prefix="/api", tags=["runner"])


class HeartbeatRequest(BaseModel):
    """A worker's heartbeat: the lease it inherited at spawn (``BLIZZARD_LEASE_ID``)."""

    lease_id: str


class HeartbeatResponse(BaseModel):
    """The recorded acknowledgement (openapi-ts consumes this)."""

    recorded: bool
    lease_id: str


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(request_body: HeartbeatRequest, request: Request) -> HeartbeatResponse:
    """Record a lease heartbeat, stamped with the injected clock."""
    store: IWriteRunnerStore | None = getattr(request.app.state, "runner_store", None)
    clock: IClock | None = getattr(request.app.state, "clock", None)
    if store is None or clock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    store.record_heartbeat(lease_id=request_body.lease_id, beat_at=clock.now())
    return HeartbeatResponse(recorded=True, lease_id=request_body.lease_id)
