"""The runner-local session-end endpoint — ``POST /api/leases/{lease_id}/session-end``.

A worker's Claude Code ``SessionEnd`` hook runs ``blizzard runner session-end`` when its
session exits naturally, which posts here with the lease id it inherited from the spawn
environment (``BLIZZARD_LEASE_ID``). The daemon appends a durable session-end fact — the
"declared done" signal (exit-is-done) — that startup crash-recovery reads to tell a
worker killed mid-work (no fact, resume) from one that cleanly exited (fact, judge) after an
involuntary restart. The CLI is a pure
client; it never opens the store itself.

Recorded unconditionally, like the heartbeat — a fact, not a status: the lease may already
be closed (the runner advanced before the hook landed) or the write may replay, and either
is harmless because crash-recovery only reads a session-end against a still-active lease. The
edge is read-only over its wiring (``bzh:controller-read-only``): it records through the store
the ``host`` composition root wired on ``app.state``. On the store-free app (OpenAPI export /
unit tests) the store is unwired and the probe answers 503 rather than pretending.
"""

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
