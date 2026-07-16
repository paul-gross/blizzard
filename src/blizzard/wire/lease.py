"""Lease wire bodies — the runner panel's read model (issue #28).

The runner-local, hub-free surface (design/runner/api.md): ``GET /api/leases`` lists
every active lease with its derived state (``bzh:facts-not-status``) — an active
lease *is* an active agent. Modeled on ``wire/runner.py``'s ``RunnerView``/
``RunnerListResponse`` shape.

Issue #29 widens the route to active + recently-closed leases:
``LeaseView`` gains ``closed_at``/``closure_reason``, both ``None`` iff the lease is
active, and ``state`` gains the sixth ``"closed"`` value.

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
    """One lease — active or recently-closed — with its joined binding facts and
    derived state (issue #28; closed leases added issue #29)."""

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
    closed_at: str | None
    closure_reason: str | None


class LeaseListResponse(BaseModel):
    """Active leases, then recently-closed ones — the panel's list (issue #28/#29)."""

    items: list[LeaseView] = []
