"""The runner-local session-end endpoint — ``POST /api/leases/{lease_id}/session-end``.

Appends a durable session-end fact — the "declared done" signal crash-recovery reads to
tell a worker killed mid-work from one that cleanly exited. Recorded unconditionally, a
fact and not a status, so a replay or an already-closed lease is harmless."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard.runner.api.wiring import RunnerWiring

router = APIRouter(prefix="/api", tags=["runner"])


class SessionEndResponse(BaseModel):
    """The recorded acknowledgement."""

    recorded: bool
    lease_id: str


@router.post("/leases/{lease_id}/session-end", response_model=SessionEndResponse)
def session_end(lease_id: str, request: Request) -> SessionEndResponse:
    """Record a lease's session-end, stamped with the injected clock."""
    RunnerWiring.of(request).lease_sessions().record_session_end(lease_id)
    return SessionEndResponse(recorded=True, lease_id=lease_id)
