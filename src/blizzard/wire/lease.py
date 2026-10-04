"""Lease wire bodies — the runner-local lease read.

``closed_at``/``closure_reason`` are both ``None`` iff the lease is active, and ``state``
carries ``"backing-off"`` for a lease held off between attempts. Datetimes
are ISO-8601 strings that **always carry an explicit UTC offset**
(``2026-07-16T12:00:00+00:00``) — ``bzh:utc-instants``.
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.foundation.leases import LeaseState


class LeaseView(BaseModel):
    """One lease — active or recently-closed — with its joined binding facts and
    derived state."""

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    session_id: str | None
    harness_id: str | None = None
    pid: int | None
    environment_id: str | None
    workdir: str | None
    created_at: str
    last_heartbeat_at: str | None
    state: LeaseState
    closed_at: str | None
    closure_reason: str | None
    #: How long a running lease may go without a heartbeat before it reads ``stale``.
    stale_after_seconds: int


class LeaseListResponse(BaseModel):
    """Active leases, then recently-closed ones."""

    items: list[LeaseView] = []
