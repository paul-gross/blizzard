"""The runner-local session-end endpoint — ``POST /api/leases/{lease_id}/session-end``.

Appends a durable session-end fact — the "declared done" signal crash-recovery reads to
tell a worker killed mid-work from one that cleanly exited. Recorded unconditionally, a
fact and not a status, so a replay or an already-closed lease is harmless. Unwired: 503."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.foundation.clock import IClock
from blizzard.runner.store.repository import IWriteRunnerStore

router = APIRouter(prefix="/api", tags=["runner"])


class SessionEndResponse(BaseModel):
    """The recorded acknowledgement (openapi-ts consumes this)."""

    recorded: bool
    lease_id: str


@router.post("/leases/{lease_id}/session-end", response_model=SessionEndResponse)
def session_end(lease_id: str, request: Request) -> SessionEndResponse:
    """Record a lease's session-end, stamped with the injected clock."""
    store: IWriteRunnerStore | None = getattr(request.app.state, "runner_store", None)
    clock: IClock | None = getattr(request.app.state, "clock", None)
    if store is None or clock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    store.record_session_end(lease_id=lease_id, ended_at=clock.now())
    return SessionEndResponse(recorded=True, lease_id=lease_id)
