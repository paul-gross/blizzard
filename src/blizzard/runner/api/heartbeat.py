"""The runner-local heartbeat endpoint — ``POST /api/heartbeat``.

Posted with the lease id the caller inherited from its spawn environment
(``BLIZZARD_LEASE_ID``); the daemon appends a heartbeat fact to its store and is the
only writer of that file, read-only over its wiring (``bzh:controller-read-only``)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from blizzard.runner.api.wiring import RunnerWiring

router = APIRouter(prefix="/api", tags=["runner"])


class HeartbeatRequest(BaseModel):
    """A worker's heartbeat: the lease it inherited at spawn (``BLIZZARD_LEASE_ID``)."""

    lease_id: str


class HeartbeatResponse(BaseModel):
    """The recorded acknowledgement."""

    recorded: bool
    lease_id: str


@router.post("/heartbeat", response_model=HeartbeatResponse)
def heartbeat(request_body: HeartbeatRequest, request: Request) -> HeartbeatResponse:
    """Record a lease heartbeat, stamped with the injected clock."""
    wiring = RunnerWiring.of(request)
    leases, clock = wiring.stores().leases, wiring.clock()
    leases.record_heartbeat(lease_id=request_body.lease_id, beat_at=clock.now())
    return HeartbeatResponse(recorded=True, lease_id=request_body.lease_id)
