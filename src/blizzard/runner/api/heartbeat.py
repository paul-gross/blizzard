"""The runner-local heartbeat endpoint — ``POST /api/heartbeat``.

A worker heartbeats as a side effect of working: its ``PostToolUse`` hook runs
``blizzard runner heartbeat`` on every tool call, which posts here with the lease id
it inherited from the spawn environment (``BLIZZARD_LEASE_ID``). The daemon appends a
heartbeat fact to its store — the only writer of that file (D-023) — and REAP reads
the last beat to catch a stalled-but-alive worker (design/runner/loop.md). The CLI is
a pure client; it never opens the store itself.

The edge is read-only over its wiring (``bzh:controller-read-only``): it records
through the store the ``host`` composition root wired on ``app.state``. On the
store-free app (OpenAPI export / unit tests) the store is unwired and the probe
answers 503 rather than pretending — the daemon's ``host`` path always wires one.
"""

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
    """Record a lease heartbeat, stamped with the injected clock (D-069)."""
    store: IWriteRunnerStore | None = getattr(request.app.state, "runner_store", None)
    clock: IClock | None = getattr(request.app.state, "clock", None)
    if store is None or clock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    store.record_heartbeat(lease_id=request_body.lease_id, beat_at=clock.now())
    return HeartbeatResponse(recorded=True, lease_id=request_body.lease_id)
