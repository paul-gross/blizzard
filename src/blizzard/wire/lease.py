"""Lease wire bodies — the runner-local lease read.

``closed_at``/``closure_reason`` are both ``None`` iff the lease is active, and ``state``
carries ``"backing-off"`` for a lease held off between attempts. Datetimes are ISO-8601 strings
that **always carry an explicit UTC offset** (``2026-07-16T12:00:00+00:00``) — ``bzh:utc-instants``."""

from __future__ import annotations

from typing import Annotated

from blizzard.foundation.leases import LeaseDisplayStatus, LeaseState
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class LeaseView(WireModel):
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
    state: Annotated[LeaseState | Unrecognized, OPEN]
    #: The lease's displayed status: ``state``, or the chunk's open takeover, escalation, or ask that outranks it.
    display_status: Annotated[LeaseDisplayStatus | Unrecognized, OPEN]
    #: Whether a closed lease ended healthily (``transitioned``); ``False`` for an active one.
    closure_healthy: bool
    closed_at: str | None
    closure_reason: str | None
    #: The chunk's project, as the hub names it — for a closed lease too; ``None`` until a binding names one.
    project_id: str | None = None
    #: How long a running lease may go without a heartbeat before it reads ``stale``.
    stale_after_seconds: int


class LeaseListResponse(WireModel):
    """Active leases, then recently-closed ones."""

    items: list[LeaseView] = []
