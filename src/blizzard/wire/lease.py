"""Lease wire bodies — the runner panel's read model (issue #28).

The runner-local, hub-free surface (design/runner/api.md): ``GET /api/leases`` lists
every active lease with its derived state (``bzh:facts-not-status``) — an active
lease *is* an active agent. Modeled on ``wire/runner.py``'s ``RunnerView``/
``RunnerListResponse`` shape.

Datetimes are ISO-8601 strings that **always carry an explicit UTC offset**
(``2026-07-16T12:00:00+00:00``), serialized via ``foundation/store/utc.py``'s
``iso_utc`` (``api/leases.py``). This is deliberate and load-bearing: the panel derives
heartbeat **age** as ``Date.now() - new Date(last_heartbeat_at)``, and JavaScript reads
an offset-less ISO string as **local** time — so a naive string skews every age by the
reader's UTC offset (silently, and invisibly to any backend test).
"""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.runner.domain.leases import LeaseState


class LeaseView(BaseModel):
    """One active lease with its joined binding facts and derived state (issue #28)."""

    lease_id: str
    chunk_id: str
    graph_id: str
    node_id: str
    node_name: str
    epoch: int
    session_id: str | None
    pid: int | None
    environment_id: str | None
    workdir: str | None
    created_at: str
    last_heartbeat_at: str | None
    state: LeaseState


class LeaseListResponse(BaseModel):
    """Every active lease, derived at read time (issue #28)."""

    items: list[LeaseView] = []
